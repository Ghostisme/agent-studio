"""MySQL 连接池与 ip_blocklist 表的 CRUD。

表结构（自动建表）：
    ip_blocklist(id, ip, reason, country_code, auto_blocked, blocked_at)

设计原则：
- 使用 aiomysql 连接池，与 FastAPI 的 async 事件循环完全兼容。
- 连接池在应用启动时通过 init_pool() 初始化，关闭时调用 close_pool()。
- 所有写操作用 INSERT … ON DUPLICATE KEY UPDATE，保证幂等。
"""

from __future__ import annotations

import logging
import os

import aiomysql

logger = logging.getLogger("db")

# 全局连接池，由 init_pool() 赋值
_pool: aiomysql.Pool | None = None


async def init_pool() -> None:
    """应用启动时调用：建立连接池并确保表存在。"""
    global _pool
    _pool = await aiomysql.create_pool(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        db=os.getenv("MYSQL_DB", "agent_studio"),
        autocommit=True,
        minsize=1,
        maxsize=5,
        charset="utf8mb4",
    )
    await _ensure_table()
    logger.info("MySQL pool ready (host=%s db=%s)", os.getenv("MYSQL_HOST"), os.getenv("MYSQL_DB"))


async def close_pool() -> None:
    """应用关闭时调用：优雅释放连接池。"""
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        logger.info("MySQL pool closed")


async def _ensure_table() -> None:
    """建表（若不存在）。"""
    async with _pool.acquire() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ip_blocklist (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    ip            VARCHAR(45)  NOT NULL,
                    reason        VARCHAR(255) DEFAULT '',
                    country_code  VARCHAR(2)   DEFAULT '',
                    auto_blocked  TINYINT(1)   DEFAULT 1,
                    blocked_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_ip (ip)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )


async def load_all_blocked() -> set[str]:
    """启动时将数据库中全部封锁 IP 加载到内存集合，避免每次请求走 DB。"""
    async with _pool.acquire() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute("SELECT ip FROM ip_blocklist")
            rows = await cur.fetchall()
            return {row[0] for row in rows}


async def persist_block(
    ip: str,
    reason: str = "",
    country_code: str = "",
    auto: bool = True,
) -> None:
    """将封锁记录写入 MySQL（幂等，已存在则更新时间和原因）。"""
    async with _pool.acquire() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO ip_blocklist (ip, reason, country_code, auto_blocked)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    reason       = VALUES(reason),
                    country_code = VALUES(country_code),
                    blocked_at   = NOW()
                """,
                (ip, reason, country_code, 1 if auto else 0),
            )


async def remove_block(ip: str) -> None:
    """从数据库删除封锁记录。"""
    async with _pool.acquire() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM ip_blocklist WHERE ip = %s", (ip,))
