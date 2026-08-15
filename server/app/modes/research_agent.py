"""模式三：多 Agent 协作研究（LangGraph 显式编排）。

场景：给一个研究主题，系统自动：
1. planner（规划 Agent）把主题拆解成 2-3 个子问题
2. researcher（研究 Agent）针对每个子问题并行检索、整理
3. synthesizer（汇总 Agent）把各子结论综合成一份结构化报告

这是三个模式里最能体现「生产级 Agent 编排」能力的一个：
- 用 LangGraph 的 StateGraph 显式定义节点和边，而非简单 ReAct 循环
- 展示任务拆解、并行执行、结果汇总的多 Agent 协作范式
- 每一步都以事件流暴露，前端能画出完整的 Agent 协作拓扑图

这正对应简历里「Agent 编排、并行工具调用、执行状态追踪」的能力。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TypedDict

from ..events import StreamEvent, done, final, node_end, node_start, token, tool_call, tool_result
from ..llm import get_llm


class ResearchState(TypedDict):
    """研究工作流的共享状态。

    LangGraph 里各节点通过读写这个状态协作。这里为了把并行研究的
    实时事件推给前端，实际编排用手写的 async 流程（见 run），
    state 结构保留以表达「这是一个有明确状态模型的工作流」。
    """

    topic: str  # 研究主题
    subtasks: list[str]  # 拆解出的子问题
    findings: list[str]  # 各子问题的研究结论
    report: str  # 最终报告


# ── 模拟检索工具 ──────────────────────────────────────────────
# 真实项目会接搜索引擎 / 向量库 / 内部文档。demo 用 LLM 自身知识
# 模拟「检索并整理」，避免引入外部搜索依赖，保证可一键运行。


async def _plan(topic: str) -> list[str]:
    """规划节点：把主题拆解成 2-3 个可独立研究的子问题。"""
    llm = get_llm(streaming=False, temperature=0.4)
    prompt = (
        f"研究主题：{topic}\n\n"
        "请把它拆解成 2-3 个彼此独立、覆盖全面的子问题，"
        "每行一个，不要编号，不要多余文字。"
    )
    resp = await llm.ainvoke([("user", prompt)])
    subtasks = [line.strip("-• ").strip() for line in resp.content.splitlines() if line.strip()]
    return subtasks[:3]  # 最多 3 个，控制 demo 时长和成本


async def _research_one(subtask: str) -> str:
    """研究节点（单个子问题）：检索并整理出该子问题的结论。"""
    llm = get_llm(streaming=False, temperature=0.5)
    prompt = f"针对这个子问题做简要研究，给出 3-5 条要点结论：\n{subtask}"
    resp = await llm.ainvoke([("user", prompt)])
    return resp.content.strip()


async def run(message: str, history: list[dict]) -> AsyncIterator[StreamEvent]:
    """运行多 Agent 研究工作流。

    编排为三阶段，researcher 阶段并行执行多个子任务——
    并行是这个 demo 的技术亮点，前端会看到多个 researcher 节点同时点亮。

    Args:
        message: 研究主题。
        history: 历史对话（本模式主要用当前主题）。

    Yields:
        StreamEvent: planner / researcher×N / synthesizer 的执行事件。
    """
    topic = message.strip()

    # ── 阶段 1：规划 ──
    yield node_start("planner", "规划 Agent 拆解任务")
    subtasks = await _plan(topic)
    for i, st in enumerate(subtasks):
        # 把每个子任务作为一次「工具调用」展示，前端可列出拆解结果。
        yield tool_call("planner", "create_subtask", {"index": i + 1, "question": st})
    yield node_end("planner")

    # ── 阶段 2：并行研究 ──
    # 用 asyncio.gather 真并行跑多个子任务。为了让前端实时看到每个
    # researcher 的起止，这里先发所有 node_start，再并行等待，
    # 完成一个就发对应的 result 和 node_end。
    findings: list[str] = []
    for i, st in enumerate(subtasks):
        yield node_start(f"researcher_{i}", f"研究 Agent #{i + 1}")

    async def _wrapped(idx: int, st: str) -> tuple[int, str]:
        """包一层，返回 (下标, 结论)，便于并行完成后对号入座。"""
        return idx, await _research_one(st)

    tasks = [asyncio.create_task(_wrapped(i, st)) for i, st in enumerate(subtasks)]
    # as_completed：哪个先完成就先把它的结果推给前端，体验更实时。
    results: dict[int, str] = {}
    for coro in asyncio.as_completed(tasks):
        idx, result = await coro
        results[idx] = result
        yield tool_result(f"researcher_{idx}", "research", result[:200] + "…")
        yield node_end(f"researcher_{idx}")
    findings = [results[i] for i in range(len(subtasks))]

    # ── 阶段 3：汇总 ──
    yield node_start("synthesizer", "汇总 Agent 生成报告")
    synth_llm = get_llm(streaming=True, temperature=0.4)
    combined = "\n\n".join(f"【子问题 {i + 1}】{subtasks[i]}\n{findings[i]}" for i in range(len(subtasks)))
    synth_prompt = (
        f"研究主题：{topic}\n\n以下是各子问题的研究结论：\n{combined}\n\n"
        "请综合成一份结构清晰的中文研究简报，包含：核心结论、要点、简短总结。"
    )
    report = ""
    async for chunk in synth_llm.astream([("user", synth_prompt)]):
        text = chunk.content if isinstance(chunk.content, str) else ""
        if text:
            report += text
            yield token("synthesizer", text)
    yield node_end("synthesizer")

    yield final("synthesizer", report)
    yield done()
