"""模式一：客服 Agent（RAG 检索 + 工具调用）。

场景：企业客服机器人。用户提问后，Agent 自主决定：
- 需要产品/政策知识 → 检索内置知识库（模拟 RAG）
- 需要实时数据（订单、物流）→ 调用业务工具
- 信息够了 → 生成最终回答

这里用 LangGraph 的 create_react_agent 构建标准的 ReAct 循环
（推理→行动→观察→再推理），并把执行过程逐事件推给前端。

为什么用 ReAct：客服场景的典型形态就是「按需取数再回答」，
ReAct 是最贴合、最经得起客户审视的成熟范式。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from ..events import StreamEvent, done, final, node_end, node_start, token, tool_call, tool_result
from ..llm import get_llm

# ── 模拟知识库 ────────────────────────────────────────────────
# 真实项目里这会是向量库检索（pgvector / Milvus 等）。demo 用内置
# 文档 + 关键词匹配模拟，避免引入向量库依赖，让客户能一键跑起来。
# 注释此处说明「真实实现会替换成什么」，体现你知道生产该怎么做。
_KNOWLEDGE_BASE = {
    "退货政策": "本店支持 7 天无理由退货，商品需保持完好、不影响二次销售。生鲜类目不支持退货。",
    "运费规则": "订单满 99 元包邮，未满收取 10 元运费。偏远地区（新疆/西藏）加收 15 元。",
    "会员权益": "PLUS 会员享全场 95 折、每月 3 张免运费券、专属客服通道。",
    "配送时效": "现货商品 48 小时内发货，一般 2-4 天送达；预售商品以商品页标注时间为准。",
}

# 模拟订单数据，供工具查询。真实项目会调后端订单服务 API。
_ORDERS = {
    "SO202608001": {"status": "已发货", "carrier": "顺丰", "tracking": "SF1234567890"},
    "SO202608002": {"status": "备货中", "carrier": None, "tracking": None},
}


@tool
def search_knowledge_base(query: str) -> str:
    """检索企业知识库，返回与问题最相关的政策/规则条目。

    用于回答退货、运费、会员、配送等政策性问题。

    Args:
        query: 用户问题或关键词。

    Returns:
        命中的知识条目文本；未命中时返回提示语。
    """
    # 简单关键词匹配模拟 RAG 召回。生产环境替换为向量相似度检索。
    hits = [content for key, content in _KNOWLEDGE_BASE.items() if key in query or query in content]
    if not hits:
        # 未命中时回退到全量拼接，让 LLM 自己判断，避免直接答「不知道」。
        hits = list(_KNOWLEDGE_BASE.values())
    return "\n".join(hits)


@tool
def query_order(order_id: str) -> str:
    """按订单号查询订单的实时状态、承运商和运单号。

    Args:
        order_id: 订单号，形如 SO202608001。

    Returns:
        订单状态描述；订单不存在时返回提示。
    """
    order = _ORDERS.get(order_id.strip().upper())
    if not order:
        return f"未查询到订单 {order_id}，请核对订单号。"
    if order["status"] == "已发货":
        return f"订单 {order_id} 状态：{order['status']}，{order['carrier']} 承运，运单号 {order['tracking']}。"
    return f"订单 {order_id} 状态：{order['status']}。"


_SYSTEM_PROMPT = """你是一个专业的电商客服助手。请遵守：
1. 涉及政策（退货/运费/会员/配送）时，务必先调用 search_knowledge_base 检索，依据检索结果回答，不要编造。
2. 涉及具体订单时，调用 query_order 查询实时状态。
3. 回答简洁、友好、准确。用中文回答。"""


async def run(message: str, history: list[dict]) -> AsyncIterator[StreamEvent]:
    """运行客服 Agent，以事件流形式产出执行过程。

    Args:
        message: 用户本轮输入。
        history: 历史对话，形如 [{"role": "user"/"assistant", "content": ...}]，
            用于维持多轮上下文。

    Yields:
        StreamEvent: 节点开始/工具调用/工具结果/Token/最终答案等事件。
    """
    llm = get_llm(streaming=True)
    tools = [search_knowledge_base, query_order]
    agent = create_react_agent(llm, tools)

    # 把前端传来的历史 + 系统提示 + 本轮输入组装成消息列表。
    messages: list = [("system", _SYSTEM_PROMPT)]
    for turn in history:
        role = "user" if turn["role"] == "user" else "assistant"
        messages.append((role, turn["content"]))
    messages.append(("user", message))

    yield node_start("agent", "客服 Agent 思考中")

    final_text = ""
    # astream_events 是 LangGraph/LangChain 的统一事件流接口，
    # 能拿到模型 token、工具调用、工具结果等细粒度事件。
    # 我们把这些底层事件翻译成我们自己的 StreamEvent 协议。
    async for ev in agent.astream_events({"messages": messages}, version="v2"):
        kind = ev["event"]

        if kind == "on_chat_model_stream":
            # 模型流式输出的一个片段。
            chunk = ev["data"]["chunk"]
            text = chunk.content if isinstance(chunk.content, str) else ""
            if text:
                final_text += text
                yield token("agent", text)

        elif kind == "on_tool_start":
            # Agent 决定调用工具。
            yield tool_call("agent", ev["name"], ev["data"].get("input", {}))

        elif kind == "on_tool_end":
            # 工具返回结果。
            yield tool_result("agent", ev["name"], ev["data"].get("output", ""))

    yield node_end("agent")
    yield final("agent", final_text)
    yield done()
