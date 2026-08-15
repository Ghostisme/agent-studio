"""FastAPI 应用入口。

对外暴露一个统一的流式对话接口 /api/chat，通过 mode 字段路由到
三种 Agent 模式之一。所有模式共用同一套 SSE 事件协议，因此前端
只需一套渲染逻辑即可适配全部模式——这是「协议先行」设计的收益。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# 必须在导入依赖环境变量的模块前加载 .env。
load_dotenv()

from .events import error, done  # noqa: E402
from .modes import data_agent, research_agent, support_agent  # noqa: E402

app = FastAPI(title="Agent Studio", version="1.0.0")

# 允许前端跨域访问。CORS_ORIGINS 支持逗号分隔多个来源。
_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 模式名 → 处理函数的路由表。新增模式只需在此注册，符合开闭原则。
_MODES = {
    "support": support_agent.run,
    "data": data_agent.run,
    "research": research_agent.run,
}


class ChatRequest(BaseModel):
    """对话请求体。

    Attributes:
        mode: Agent 模式，取值 support / data / research。
        message: 用户本轮输入。
        history: 历史对话列表，用于多轮上下文。
    """

    mode: str
    message: str
    history: list[dict] = []


@app.get("/api/health")
async def health() -> dict:
    """健康检查。用于部署平台探活和前端连通性自检。"""
    return {"status": "ok"}


@app.get("/api/modes")
async def list_modes() -> dict:
    """返回可用模式列表及展示信息，供前端渲染模式切换 Tab。"""
    return {
        "modes": [
            {
                "key": "support",
                "name": "Customer Support Agent",
                "desc": "RAG 知识检索 + 工具调用的客服机器人",
            },
            {
                "key": "data",
                "name": "Data Analysis Agent",
                "desc": "自然语言 → SQL → 执行 → 洞察",
            },
            {
                "key": "research",
                "name": "Multi-Agent Research",
                "desc": "规划 → 并行研究 → 汇总的多 Agent 协作",
            },
        ]
    }


async def _event_stream(req: ChatRequest) -> AsyncIterator[str]:
    """把选中模式的事件流转成 SSE 文本流。

    统一在此捕获异常并转成 error 事件，保证前端永远收到结构化的
    失败原因和结束信号，而不是连接被硬断——这对 UI 的健壮性很关键。
    """
    handler = _MODES.get(req.mode)
    if handler is None:
        yield error(f"未知模式：{req.mode}").to_sse()
        yield done().to_sse()
        return

    try:
        async for ev in handler(req.message, req.history):
            yield ev.to_sse()
    except Exception as e:  # noqa: BLE001 - demo 中统一兜底，避免裸 500
        yield error(f"执行出错：{e}").to_sse()
        yield done().to_sse()


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """统一的流式对话接口。

    返回 text/event-stream，前端用 EventSource 或 fetch+ReadableStream 消费。
    关闭 Nginx 缓冲（X-Accel-Buffering）以保证流式实时性。
    """
    return StreamingResponse(
        _event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
