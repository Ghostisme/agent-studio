"""IP 地理位置查询模块。

使用 ip-api.com 免费接口（无需 API Key，45 req/min 限制）。
通过进程内缓存确保同一 IP 只查询一次，避免频繁调用外部服务。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger("geo")

# 进程内缓存：ip -> ISO country code（空字符串表示查询失败或未知）
_cache: dict[str, str] = {}
# 防止同一 IP 并发多次查询
_in_flight: dict[str, asyncio.Event] = {}


async def get_country(ip: str) -> str:
    """返回 IP 对应的 ISO 国家代码（如 "CN"），查询失败返回空字符串。

    同一 IP 并发查询时只发出一个 HTTP 请求，其余等待结果复用。
    """
    if ip in _cache:
        return _cache[ip]

    # 等待已有的在途查询完成
    if ip in _in_flight:
        await _in_flight[ip].wait()
        return _cache.get(ip, "")

    event = asyncio.Event()
    _in_flight[ip] = event
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{ip}",
                params={"fields": "status,countryCode"},
            )
            data = resp.json()
            code = data.get("countryCode", "") if data.get("status") == "success" else ""
    except Exception as exc:
        logger.debug("Geo lookup failed for %s: %s", ip, exc)
        code = ""

    _cache[ip] = code
    event.set()
    _in_flight.pop(ip, None)
    return code
