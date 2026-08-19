"""IP 封锁与速率限制守卫（MySQL 持久化 + 地理封锁版）。

运行时流程：
1. 应用启动时调用 guard.load_from_db()，将数据库黑名单全量加载到内存集合。
2. 每次请求先走内存检查（<1μs），无需数据库 I/O。
3. 超速或来自封锁地区的 IP 会被自动加入内存集合并异步持久化到 MySQL。
4. 重启后从数据库恢复黑名单，不丢失历史封锁记录。

可配置环境变量：
    RATE_WINDOW_SEC      滑动窗口时长（默认 60s）
    RATE_MAX_REQUESTS    窗口内最大请求数（默认 30）
    BLOCKED_IPS          预置黑名单，逗号分隔
    BLOCK_CN_IPS         设为 "true" 则大陆 IP 首次访问即封锁（默认 true）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from threading import Lock

from .db import load_all_blocked, persist_block, remove_block
from .geo import get_country

logger = logging.getLogger("guard")

_WINDOW_SEC: int = int(os.getenv("RATE_WINDOW_SEC", "60"))
_MAX_REQUESTS: int = int(os.getenv("RATE_MAX_REQUESTS", "30"))
_BLOCK_CN: bool = os.getenv("BLOCK_CN_IPS", "true").lower() == "true"

# 预置黑名单（环境变量）
_ENV_BLOCKED: set[str] = {
    ip.strip() for ip in os.getenv("BLOCKED_IPS", "").split(",") if ip.strip()
}

# 封锁地区列表（ISO 代码），可通过 BLOCKED_COUNTRIES 扩展
_BLOCKED_COUNTRIES: set[str] = {
    c.strip().upper()
    for c in os.getenv("BLOCKED_COUNTRIES", "CN").split(",")
    if c.strip()
} if _BLOCK_CN else set()


class IPGuard:
    """线程安全的内存速率跟踪 + MySQL 持久化黑名单管理器。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, list[float]] = defaultdict(list)
        # 内存黑名单（快速路径），启动时从 DB 加载
        self._blocklist: set[str] = set(_ENV_BLOCKED)

    # ── 启动钩子 ──────────────────────────────────────────────────────────────

    async def load_from_db(self) -> None:
        """从数据库加载全量黑名单到内存，在应用 lifespan 启动阶段调用。"""
        db_blocked = await load_all_blocked()
        with self._lock:
            self._blocklist.update(db_blocked)
        total = len(self._blocklist)
        logger.info("黑名单已从 MySQL 加载，共 %d 条", total)

    # ── 主请求检查（中间件调用） ───────────────────────────────────────────────

    def is_blocked(self, ip: str) -> bool:
        """同步内存检查，每次请求都会调用，必须保持极低延迟。"""
        return ip in self._blocklist

    async def check_and_record(self, ip: str) -> tuple[bool, str]:
        """异步完整检查：地理位置 + 速率限制。

        Returns:
            (should_block, reason)
            should_block=True 表示该请求应被拒绝。
        """
        # 地理位置检查（结果有缓存，二次调用无 HTTP 开销）
        if _BLOCKED_COUNTRIES:
            country = await get_country(ip)
            if country in _BLOCKED_COUNTRIES:
                await self._block(ip, reason=f"geo:{country}", country_code=country)
                return True, f"blocked country ({country})"

        # 速率限制检查（纯内存，线程安全）
        with self._lock:
            if ip in self._blocklist:
                return True, "blocklist"

            now = time.monotonic()
            window_start = now - _WINDOW_SEC
            ts = self._counters[ip]
            ts[:] = [t for t in ts if t > window_start]
            ts.append(now)

            if len(ts) > _MAX_REQUESTS:
                # 先在锁内加入内存集合，再异步持久化（持久化在锁外）
                self._blocklist.add(ip)
                should_persist = True
            else:
                should_persist = False

        if should_persist:
            reason = f"rate_limit:{len(self._counters[ip])}/{_WINDOW_SEC}s"
            logger.warning("🚫 自动封锁 IP=%s  原因=%s", ip, reason)
            # 持久化不阻塞请求，后台完成即可
            asyncio.create_task(persist_block(ip, reason=reason, auto=True))
            return True, reason

        return False, ""

    # ── 管理操作 ──────────────────────────────────────────────────────────────

    async def manual_block(self, ip: str, reason: str = "manual") -> None:
        """手动封锁：写入内存 + 持久化 MySQL。"""
        with self._lock:
            self._blocklist.add(ip)
        logger.warning("🚫 手动封锁 IP=%s", ip)
        await persist_block(ip, reason=reason, auto=False)

    async def manual_unblock(self, ip: str) -> None:
        """解封：从内存 + MySQL 同时移除。"""
        with self._lock:
            self._blocklist.discard(ip)
        logger.info("✅ 解除封锁 IP=%s", ip)
        await remove_block(ip)

    def blocked_list(self) -> list[str]:
        with self._lock:
            return sorted(self._blocklist)

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    async def _block(self, ip: str, reason: str, country_code: str = "") -> None:
        """加入内存集合并后台持久化，避免重复持久化已封锁的 IP。"""
        with self._lock:
            if ip in self._blocklist:
                return
            self._blocklist.add(ip)
        logger.warning("🚫 自动封锁 IP=%s  原因=%s", ip, reason)
        asyncio.create_task(
            persist_block(ip, reason=reason, country_code=country_code, auto=True)
        )


guard = IPGuard()
