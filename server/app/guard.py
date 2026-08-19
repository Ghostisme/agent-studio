"""IP 封锁与速率限制守卫。

设计思路：
- 维护一个内存中的 IP 请求计数器（滑动窗口）。
- 超过阈值后自动将该 IP 加入永久黑名单（本次进程内）。
- 黑名单也支持从环境变量 BLOCKED_IPS（逗号分隔）预先导入，
  方便在配置层面直接封锁已知恶意 IP。
- 所有封锁事件写入标准日志，便于接入外部监控（Datadog/Sentry/ELK）。
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from threading import Lock

logger = logging.getLogger("guard")

# ── 配置（可通过环境变量覆盖） ────────────────────────────────────────────────
# 滑动窗口时长（秒）
_WINDOW_SEC: int = int(os.getenv("RATE_WINDOW_SEC", "60"))
# 窗口内允许的最大请求数；超过则自动封锁
_MAX_REQUESTS: int = int(os.getenv("RATE_MAX_REQUESTS", "30"))
# 预置黑名单，从环境变量读取（逗号分隔 IP 列表）
_ENV_BLOCKED: set[str] = {
    ip.strip() for ip in os.getenv("BLOCKED_IPS", "").split(",") if ip.strip()
}


class IPGuard:
    """线程安全的 IP 速率跟踪 + 黑名单管理器。"""

    def __init__(self) -> None:
        self._lock = Lock()
        # { ip: [(timestamp, count), ...] }  滑动窗口请求记录
        self._counters: dict[str, list[float]] = defaultdict(list)
        # 永久黑名单
        self._blocklist: set[str] = set(_ENV_BLOCKED)
        if _ENV_BLOCKED:
            logger.warning("预置黑名单已加载: %s", _ENV_BLOCKED)

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def is_blocked(self, ip: str) -> bool:
        """检查 IP 是否在黑名单中。"""
        return ip in self._blocklist

    def record_and_check(self, ip: str) -> bool:
        """记录一次请求并判断是否需要封锁。

        Returns:
            True  → 该 IP 已被（或刚被）封锁，调用方应拒绝请求。
            False → 正常放行。
        """
        with self._lock:
            if ip in self._blocklist:
                return True

            now = time.monotonic()
            window_start = now - _WINDOW_SEC

            # 清除窗口外的旧记录
            timestamps = self._counters[ip]
            timestamps[:] = [t for t in timestamps if t > window_start]
            timestamps.append(now)

            if len(timestamps) > _MAX_REQUESTS:
                self._blocklist.add(ip)
                logger.warning(
                    "🚫 自动封锁 IP=%s  窗口=%ds 内请求数=%d（阈值=%d）",
                    ip,
                    _WINDOW_SEC,
                    len(timestamps),
                    _MAX_REQUESTS,
                )
                return True

        return False

    def manual_block(self, ip: str) -> None:
        """手动将 IP 加入黑名单。"""
        with self._lock:
            self._blocklist.add(ip)
        logger.warning("🚫 手动封锁 IP=%s", ip)

    def manual_unblock(self, ip: str) -> None:
        """从黑名单移除 IP。"""
        with self._lock:
            self._blocklist.discard(ip)
        logger.info("✅ 解除封锁 IP=%s", ip)

    def blocked_list(self) -> list[str]:
        """返回当前黑名单快照（调试 / 管理用）。"""
        with self._lock:
            return sorted(self._blocklist)


# 全局单例，整个应用共享
guard = IPGuard()
