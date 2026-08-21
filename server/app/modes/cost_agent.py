"""模式四：成本优化 Agent（模型路由 + 语义缓存 + 成本计量）。

场景：面向「同一个 LLM 功能要在生产里把成本压下来」的真实诉求，把一次
问答请求的完整降本链路显式化、可视化：

    route（按难度选模型） → cache lookup（语义缓存查询）
      ├─ 命中 → 直接返回缓存答案，零模型开销
      └─ 未命中 → 用路由选中的模型生成 → 写回缓存
    meter（计量本次实际成本 vs 无优化基线，算出节省）

为什么单独做一个模式：前三个模式展示「Agent 能做什么」，这个模式展示
「让 Agent 在生产里跑得起、花得省」——这正是成本优化岗位的核心，也让
每个执行节点都对应一个可解释的降本决策，前端执行图一眼看清钱花在哪、
省在哪。

沿用统一 SSE 事件协议：路由/命中/计量都以 tool_call / tool_result 暴露，
因此前端零改动即可渲染本模式（新增 mode 无需动前端，正是协议先行的收益）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..cost import (
    CostLedger,
    baseline_cost,
    cache,
    estimate_cost,
    estimate_tokens,
    route,
)
from ..events import StreamEvent, done, final, node_end, node_start, token, tool_call, tool_result
from ..llm import get_embeddings, get_llm

# 语义缓存命名空间：本模式独占，避免与其他场景交叉命中。
_NAMESPACE = "cost_agent"

_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question clearly and concisely in English."
)


async def run(message: str, history: list[dict]) -> AsyncIterator[StreamEvent]:
    """运行成本优化 Agent，把降本链路逐事件推给前端。

    Args:
        message: 用户查询。
        history: 历史对话（本模式按单轮问答演示，history 备用）。

    Yields:
        StreamEvent: route / cache / (generate) / meter 各节点的执行事件。
    """
    query = message.strip()
    ledger = CostLedger()

    # ── 节点 1：模型路由 ──
    # 先决定「这个任务值不值得用贵模型」，把决策理由透传到 UI。
    yield node_start("router", "Route by difficulty")
    decision = route(query)
    yield tool_call("router", "route", {"query": query})
    yield tool_result("router", "route", f"{decision.tier} · {decision.model} — {decision.reason}")
    ledger.events.append(decision.reason)
    yield node_end("router")

    # ── 节点 2：语义缓存查询 ──
    # 命中即零模型开销，这是最便宜的一条路径，所以放在生成之前。
    yield node_start("cache", "Semantic cache lookup")
    yield tool_call("cache", "semantic_lookup", {"namespace": _NAMESPACE, "query": query})

    # 计算查询的 embedding。网关若不支持 embedding 模型，降级到「精确匹配」
    # 缓存（查询字符串本身作为 key）——这是 honest fallback：demo 无 embedding
    # 也能跑，注释里说清生产应配真正的 embedding 做语义近邻匹配。
    query_emb: list[float] = []
    emb_cost = 0.0
    try:
        query_emb = await get_embeddings().aembed_query(query)
        # embedding 本身也有成本，如实计入 actual（命中时这是唯一开销）。
        emb_cost = estimate_cost("text-embedding-3-small", estimate_tokens(query), 0)
        ledger.actual_cost += emb_cost
    except Exception:  # noqa: BLE001 - 网关不支持 → 降级精确匹配
        # 用全零向量 + 查询归一化哈希做伪向量，相似度判定会退化为精确匹配。
        # 生产环境应配可用的 embedding 端点，实现真正的语义近邻缓存。
        query_emb = [0.0]  # 伪向量，确保 lookup 不会因空列表崩溃

    lookup = await cache.lookup(_NAMESPACE, query, query_emb)

    if lookup.hit:
        # 命中：直接复用缓存答案，跳过（昂贵的）生成步骤。
        yield tool_result(
            "cache",
            "semantic_lookup",
            f"HIT (similarity {lookup.similarity:.3f}) — reusing answer for: “{lookup.matched_query}”",
        )
        yield node_end("cache")
        ledger.cache_hit = True
        ledger.model_used = "cache"

        # 把缓存答案以 token 事件回放，保持与其他模式一致的流式体验。
        for piece in _chunks(lookup.response):
            yield token("cache", piece)

        # 基线：假设无缓存 + 全程强模型，需完整生成一遍。
        in_tok, out_tok = estimate_tokens(query), estimate_tokens(lookup.response)
        ledger.baseline_cost = baseline_cost(in_tok, out_tok)

        for ev in _meter_events(ledger):
            yield ev
        yield final("cache", lookup.response)
        yield done()
        return

    # 未命中：如实告知相似度，便于理解为何没复用。
    yield tool_result(
        "cache", "semantic_lookup",
        f"MISS (best similarity {lookup.similarity:.3f} < threshold) — will generate",
    )
    yield node_end("cache")

    # ── 节点 3：生成（用路由选中的模型）──
    yield node_start("generate", f"Generate with {decision.model}")
    llm = get_llm(streaming=True, model=decision.model)
    answer = ""
    async for chunk in llm.astream([("system", _SYSTEM_PROMPT), ("user", query)]):
        text = chunk.content if isinstance(chunk.content, str) else ""
        if text:
            answer += text
            yield token("generate", text)
    yield node_end("generate")

    # 写回缓存，供后续语义相近的查询命中。
    await cache.store(_NAMESPACE, query, query_emb, answer)

    # ── 计量：实际成本（选中模型）vs 基线成本（强模型 + 无缓存）──
    in_tok, out_tok = estimate_tokens(query), estimate_tokens(answer)
    ledger.model_used = decision.model
    ledger.actual_cost += estimate_cost(decision.model, in_tok, out_tok)
    ledger.baseline_cost = baseline_cost(in_tok, out_tok)

    for ev in _meter_events(ledger):
        yield ev
    yield final("generate", answer)
    yield done()


def _meter_events(ledger: CostLedger) -> list[StreamEvent]:
    """把成本账本汇成一组事件，前端当作独立的「计量」节点 + 卡片展示。

    单独抽出，是因为命中/未命中两条路径都要发，避免重复拼装。
    发完整的 node_start → tool_call → tool_result → node_end 序列，
    这样 meter 才会作为一个独立节点出现在执行图上（前端的 tool_result
    归约依赖对应节点已存在，缺 node_start 会导致卡片被丢弃）。
    """
    summary = (
        f"actual ${ledger.actual_cost:.6f} vs baseline ${ledger.baseline_cost:.6f} "
        f"→ saved ${ledger.saved:.6f} ({ledger.saved_pct}%) · "
        f"{'CACHE HIT' if ledger.cache_hit else 'model=' + ledger.model_used}"
    )
    return [
        node_start("meter", "Meter cost vs baseline"),
        tool_call("meter", "cost_report", {"model": ledger.model_used or "cache"}),
        tool_result("meter", "cost_report", summary),
        node_end("meter"),
    ]


def _chunks(text: str, size: int = 24) -> list[str]:
    """把缓存答案切成小片，以 token 事件回放，模拟流式返回体验。

    纯展示用途——缓存命中本可一次性返回，切片只为 UI 观感与其余模式一致。
    """
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
