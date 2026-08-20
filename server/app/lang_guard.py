"""语言检测中间件：检测到中文消息自动封禁 IP。

使用 langdetect 离线检测用户消息语言，若为中文（zh-cn/zh-tw）
则封禁该 IP 并持久化到 MySQL，理由标记为 lang:zh。
"""

from __future__ import annotations

import logging

from langdetect import DetectorFactory, LangDetectException, detect

from .guard import guard

logger = logging.getLogger("lang_guard")

# 固定随机种子，保证 langdetect 结果可复现（否则同一文本每次检测结果可能不同）
DetectorFactory.seed = 0


async def check_language_and_block(ip: str, message: str) -> tuple[bool, str]:
    """检测消息语言，若为中文则封禁 IP。

    Returns:
        (should_block, reason)
        should_block=True 表示该请求应被拒绝。
    """
    if not message or len(message.strip()) < 3:
        # 消息太短无法准确检测，放行
        return False, ""

    try:
        lang = detect(message)
    except LangDetectException:
        # 检测失败（如纯数字/特殊符号），放行
        return False, ""

    # zh-cn / zh-tw 都视为中文
    if lang.startswith("zh"):
        reason = f"lang:{lang}"
        logger.warning("🚫 检测到中文消息，封禁 IP=%s  语言=%s", ip, lang)
        await guard.manual_block(ip, reason=reason)
        return True, reason

    return False, ""
