"""LLM 供应商封装。

把「如何获取一个 LLM 实例」收敛到这一个地方，好处：
- 三种 Agent 模式共用同一套模型配置，改供应商/模型只改这里。
- 通过 base_url 支持任何兼容 OpenAI 协议的网关（含国产模型），
  demo 不锁死在某一家供应商上，体现工程上的可扩展设计。
"""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI


def get_llm(streaming: bool = True, temperature: float = 0.3) -> ChatOpenAI:
    """构造一个 ChatOpenAI 实例。

    Args:
        streaming: 是否开启流式输出。对话类节点开启（逐字返回体验更好），
            纯结构化决策类节点可关闭以简化处理。
        temperature: 采样温度。默认偏低（0.3）以让 demo 输出更稳定可复现。

    Returns:
        配置好的 ChatOpenAI 实例。

    Raises:
        RuntimeError: 当 OPENAI_API_KEY 未配置时，给出明确提示而非隐晦报错。
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "sk-your-key-here":
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill in a real key."
        )

    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        api_key=api_key,
        temperature=temperature,
        streaming=streaming,
    )
