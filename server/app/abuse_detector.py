"""滥用检测：规则引擎 + LLM 终审识别测试/玩闹流量。

工作流程：
1. 规则引擎快速过滤明显测试特征（重复消息、测试词、短消息）
2. 可疑但不确定的，调用 LLM 终审判断是否真实客户咨询
3. 确认滥用后封禁 IP 并持久化到 MySQL

每个 IP 的历史消息存储在内存中（进程内缓存），用于检测重复和频率。
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Lock

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .guard import guard
from .llm import get_llm

logger = logging.getLogger("abuse_detector")

# 测试关键词（不区分大小写）
TEST_KEYWORDS = {
    "test", "testing", "ping", "pong", "hello world", "hi there",
    "check", "试试", "测试", "检查", "curl", "wget", "api test"
}

# 代码/命令特征正则
CODE_PATTERNS = [
    r"curl\s+",
    r"wget\s+",
    r"http(s)?://.*\.(sh|py|js)",
    r"#!/bin/",
    r"<script>",
    r"SELECT\s+.*\s+FROM",
]


@dataclass
class MessageRecord:
    """单条消息记录。"""
    content: str
    timestamp: float


class AbuseDetector:
    """滥用检测器（线程安全）。"""

    def __init__(self) -> None:
        self._lock = Lock()
        # ip -> 最近 N 条消息记录
        self._history: dict[str, list[MessageRecord]] = defaultdict(list)
        # 每个 IP 保留最近 10 条消息
        self._max_history = 10
        # 短消息阈值（字符数）
        self._short_threshold = 10
        # 短时间窗口（秒）
        self._short_window = 300  # 5 分钟

    async def check_message(self, ip: str, message: str) -> tuple[bool, str]:
        """检测消息是否滥用。

        Returns:
            (is_abuse, reason)
            is_abuse=True 表示应封禁该 IP。
        """
        with self._lock:
            now = time.time()
            history = self._history[ip]

            # 添加当前消息到历史
            history.append(MessageRecord(content=message, timestamp=now))

            # 保留最近 N 条
            if len(history) > self._max_history:
                history.pop(0)

            # 规则 1: 重复消息检测
            recent_5min = [m for m in history if now - m.timestamp < self._short_window]
            if len(recent_5min) >= 2:
                contents = [m.content.strip().lower() for m in recent_5min]
                if len(set(contents)) == 1:
                    return True, "abuse:repeated_message"

            # 规则 2: 短消息频繁发送
            short_msgs = [
                m for m in recent_5min
                if len(m.content.strip()) < self._short_threshold
            ]
            if len(short_msgs) >= 3:
                return True, "abuse:frequent_short_messages"

            # 规则 3: 测试关键词频繁出现
            msg_lower = message.lower()
            keyword_count = sum(
                1 for m in recent_5min
                if any(kw in m.content.lower() for kw in TEST_KEYWORDS)
            )
            if keyword_count >= 3:
                return True, "abuse:test_keywords"

            # 规则 4: 代码/命令特征
            for pattern in CODE_PATTERNS:
                if re.search(pattern, message, re.IGNORECASE):
                    return True, "abuse:code_injection_attempt"

            # 规则 5: LLM 终审（可疑但不确定的情况）
            # 触发条件：5分钟内发送 >= 3 条消息，且至少 1 条包含测试关键词
            has_test_keyword = any(kw in msg_lower for kw in TEST_KEYWORDS)
            if len(recent_5min) >= 3 and has_test_keyword:
                is_abuse = await self._llm_judge(message, recent_5min)
                if is_abuse:
                    return True, "abuse:llm_confirmed"

        return False, ""

    async def _llm_judge(
        self,
        current_msg: str,
        recent_history: list[MessageRecord]
    ) -> bool:
        """LLM 终审：判断是否真实客户咨询。

        Returns:
            True = 滥用/测试流量，False = 正常咨询
        """
        # 构造历史上下文
        history_text = "\n".join([
            f"- {m.content}" for m in recent_history[-5:]  # 最近 5 条
        ])

        prompt = f"""You are a content moderator. Analyze if the following conversation is legitimate customer inquiry or just testing/playing around.

Recent messages from this user:
{history_text}

Current message:
{current_msg}

Respond with ONLY one word:
- "ABUSE" if this looks like testing, playing around, or spam
- "LEGITIMATE" if this looks like a real customer question

Consider these as ABUSE signals:
- Repetitive test phrases (test, ping, hello, check)
- Very short messages with no real question
- Random characters or nonsense
- Multiple similar greetings without follow-up

Response:"""

        try:
            llm = get_llm()
            messages = [SystemMessage(content=prompt)]
            response = await llm.ainvoke(messages)
            result = response.content.strip().upper()

            logger.info("LLM 终审结果: %s (消息: %s)", result, current_msg[:50])
            return "ABUSE" in result
        except Exception as e:
            logger.error("LLM 终审失败: %s", e)
            # LLM 失败时保守处理，不封禁
            return False


detector = AbuseDetector()
