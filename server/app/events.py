"""统一的流式事件协议。

这是整个系统前后端对齐的核心契约：后端把 LangGraph 的执行过程
（节点开始/结束、工具调用、Token 流、最终结果、错误）统一编码成
一串结构化事件，通过 SSE 推给前端；前端据此渲染对话气泡和执行链路图。

设计意图：
- 把「Agent 在干什么」变成显式、可追踪的事件流，而不是黑盒。
  这样前端能实时展示节点进度、工具调用、等待和失败原因——
  这正是生产级 Agent 产品与「套壳调 API」的关键区别。
- 事件是自描述的（带 type 和 node），前端无需知道具体是哪种
  Agent 模式，同一套渲染逻辑适配三种模式，降低耦合。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """流式事件的类型枚举。

    前端根据 type 决定如何渲染：token 追加到当前气泡，node_* 更新执行图，
    tool_* 展示工具调用卡片，error 显示失败原因，done 收尾。
    """

    NODE_START = "node_start"  # 某个 Agent 节点开始执行
    NODE_END = "node_end"  # 某个节点执行结束
    TOKEN = "token"  # LLM 流式输出的一个文本片段
    TOOL_CALL = "tool_call"  # Agent 决定调用某个工具
    TOOL_RESULT = "tool_result"  # 工具返回结果
    FINAL = "final"  # 最终答案（完整文本）
    ERROR = "error"  # 执行出错
    DONE = "done"  # 整个流结束的信号


class StreamEvent(BaseModel):
    """单条流式事件。

    Attributes:
        type: 事件类型，决定前端渲染方式。
        node: 产生该事件的 LangGraph 节点名（如 "planner"、"researcher"），
              用于在执行图上定位。顶层事件可为空。
        data: 事件负载，不同类型含义不同（token 文本 / 工具名和参数 / 结果等）。
    """

    type: EventType
    node: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """序列化为 SSE（Server-Sent Events）传输格式。

        SSE 每条消息以 ``data: <json>\\n\\n`` 结尾。这里用紧凑 JSON
        （无多余空格）减少传输体积，长对话场景下累积效果明显。

        Returns:
            符合 SSE 规范的字符串，可直接写入 StreamingResponse。
        """
        payload = self.model_dump(exclude_none=True)
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── 构造事件的便捷工厂函数 ────────────────────────────────────
# 用函数封装而非到处手写 StreamEvent(...)，避免字段名散落各处，
# 将来协议若要调整，只需改这里。


def node_start(node: str, label: str) -> StreamEvent:
    """节点开始事件。label 是给用户看的可读名称（如「规划任务」）。"""
    return StreamEvent(type=EventType.NODE_START, node=node, data={"label": label})


def node_end(node: str) -> StreamEvent:
    """节点结束事件。"""
    return StreamEvent(type=EventType.NODE_END, node=node)


def token(node: str, text: str) -> StreamEvent:
    """LLM 流式输出片段事件。"""
    return StreamEvent(type=EventType.TOKEN, node=node, data={"text": text})


def tool_call(node: str, name: str, args: dict[str, Any]) -> StreamEvent:
    """工具调用事件：Agent 决定调用名为 name 的工具，参数为 args。"""
    return StreamEvent(type=EventType.TOOL_CALL, node=node, data={"name": name, "args": args})


def tool_result(node: str, name: str, result: Any) -> StreamEvent:
    """工具结果事件。result 会被转为字符串展示，避免非可序列化对象出错。"""
    return StreamEvent(
        type=EventType.TOOL_RESULT, node=node, data={"name": name, "result": str(result)}
    )


def final(node: str, text: str) -> StreamEvent:
    """最终答案事件。"""
    return StreamEvent(type=EventType.FINAL, node=node, data={"text": text})


def error(message: str) -> StreamEvent:
    """错误事件。把异常转成用户可读的失败原因，而非直接 500。"""
    return StreamEvent(type=EventType.ERROR, data={"message": message})


def done() -> StreamEvent:
    """流结束信号，前端据此关闭连接、清理 loading 态。"""
    return StreamEvent(type=EventType.DONE)
