"""LLM 供应商封装。

把「如何获取一个 LLM 实例」收敛到这一个地方，好处：
- 所有 Agent 模式共用同一套模型配置，改供应商/模型只改这里。
- 通过 base_url 支持任何兼容 OpenAI 协议的网关（含国产模型），
  demo 不锁死在某一家供应商上，体现工程上的可扩展设计。
- 成本优化模式（cost_agent）依赖这里的「按模型名构造」和 embedding，
  实现模型路由（贵/便宜模型切换）与语义缓存——把 provider 收敛在一处，
  正是做路由/网关切换所需要的接缝。
"""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# 便宜档 / 贵档的默认模型名。
# 收敛成常量而非散落在各处，切换供应商时只改这里；成本路由（cost.py）
# 也复用这两个常量，保证「路由目标」与「实际构造」永远一致。
CHEAP_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
# 强档模型。默认用与便宜档同组的 gpt-5.4：token 权限一致、开箱可用，
# 且 5.4 / 5.4-mini 价差正好 3.33x，路由节省的故事干净可信。
# 想升到更强档（gpt-5.5 等），配 OPENAI_STRONG_MODEL 覆盖即可。
STRONG_MODEL = os.getenv("OPENAI_STRONG_MODEL", "gpt-5.4")
# 语义缓存用的 embedding 模型。text-embedding-3-small 便宜且够用，
# 命中一次可省下一次（更贵的）completion，投入产出比很高。
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")


def _require_api_key() -> str:
    """读取并校验 OPENAI_API_KEY，未配置时给出明确提示而非隐晦报错。"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "sk-your-key-here":
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill in a real key."
        )
    return api_key


def get_llm(
    streaming: bool = True,
    temperature: float = 0.3,
    model: str | None = None,
) -> ChatOpenAI:
    """构造一个 ChatOpenAI 实例。

    Args:
        streaming: 是否开启流式输出。对话类节点开启（逐字返回体验更好），
            纯结构化决策类节点可关闭以简化处理。
        temperature: 采样温度。默认偏低（0.3）以让 demo 输出更稳定可复现。
        model: 显式指定模型名。默认走 CHEAP_MODEL；成本路由决定升档到
            STRONG_MODEL 时，通过这个参数传入——这是模型路由的落点。

    Returns:
        配置好的 ChatOpenAI 实例。

    Raises:
        RuntimeError: 当 OPENAI_API_KEY 未配置时。
    """
    return ChatOpenAI(
        model=model or CHEAP_MODEL,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        api_key=_require_api_key(),
        temperature=temperature,
        streaming=streaming,
    )


def get_embeddings() -> OpenAIEmbeddings:
    """构造 embedding 实例，供语义缓存计算查询向量。

    与 get_llm 共用同一套 key / base_url 配置，确保自建网关同样生效。

    Returns:
        配置好的 OpenAIEmbeddings 实例。

    Raises:
        RuntimeError: 当 OPENAI_API_KEY 未配置时。
    """
    return OpenAIEmbeddings(
        model=EMBED_MODEL,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        api_key=_require_api_key(),
    )
