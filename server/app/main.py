"""FastAPI 应用入口。

对外暴露一个统一的流式对话接口 /api/chat，通过 mode 字段路由到
三种 Agent 模式之一。所有模式共用同一套 SSE 事件协议，因此前端
只需一套渲染逻辑即可适配全部模式——这是「协议先行」设计的收益。
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# 必须在导入依赖环境变量的模块前加载 .env。
load_dotenv()

from .db import init_pool, close_pool  # noqa: E402
from .events import error, done  # noqa: E402
from .guard import guard  # noqa: E402
from .lang_guard import check_language_and_block  # noqa: E402
from .abuse_detector import detector  # noqa: E402
from .modes import cost_agent, data_agent, research_agent, support_agent  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化 MySQL 连接池并加载黑名单，关闭时释放连接池。"""
    await init_pool()
    await guard.load_from_db()
    yield
    await close_pool()


app = FastAPI(title="Agent Studio", version="1.0.0", lifespan=lifespan)

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
    "cost": cost_agent.run,
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


def _get_client_ip(request: Request) -> str:
    """提取真实客户端 IP。

    优先读取反向代理注入的 X-Forwarded-For 首部（取最左侧非私有 IP），
    其次回退到直连 IP。部署在 Vercel/Nginx 前置代理时依赖此逻辑。
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For: client, proxy1, proxy2  → 取第一个
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def ip_guard_middleware(request: Request, call_next):
    """IP 黑名单 + 速率限制中间件。

    对每个入站请求：
    1. 提取真实 IP。
    2. 检查是否在黑名单内（直接 403）。
    3. 记录请求并判断是否超速（自动加黑并 429）。
    4. 放行时记录 INFO 日志，便于后续分析。
    """
    ip = _get_client_ip(request)

    # 快速内存检查：已封锁直接拒绝，避免触发任何 LLM 调用
    if guard.is_blocked(ip):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    # 地理位置 + 速率检测（含自动封锁 + 异步持久化到 MySQL）
    blocked, reason = await guard.check_and_record(ip)
    if blocked:
        status = 403 if "geo:" in reason else 429
        return JSONResponse(status_code=status, content={"detail": "Forbidden"})

    logger.info("→ %s %s  ip=%s", request.method, request.url.path, ip)
    return await call_next(request)


# ── 管理接口（建议生产环境用环境变量 ADMIN_TOKEN 保护） ────────────────────────
_ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "")


def _check_admin(request: Request) -> bool:
    """简单的 Bearer Token 鉴权，ADMIN_TOKEN 为空时禁用管理接口。"""
    if not _ADMIN_TOKEN:
        return False
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {_ADMIN_TOKEN}"


@app.get("/api/admin/blocklist")
async def get_blocklist(request: Request):
    """查看当前黑名单。需要 Authorization: Bearer <ADMIN_TOKEN>。"""
    if not _check_admin(request):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return {"blocked": guard.blocked_list()}


@app.post("/api/admin/block/{ip}")
async def block_ip(ip: str, request: Request):
    """手动封锁指定 IP。需要 Authorization: Bearer <ADMIN_TOKEN>。"""
    if not _check_admin(request):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    guard.manual_block(ip)
    return {"blocked": ip}


@app.delete("/api/admin/block/{ip}")
async def unblock_ip(ip: str, request: Request):
    """解除封锁指定 IP。需要 Authorization: Bearer <ADMIN_TOKEN>。"""
    if not _check_admin(request):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    guard.manual_unblock(ip)
    return {"unblocked": ip}


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
                "desc": "RAG knowledge retrieval + tool-calling support bot",
            },
            {
                "key": "data",
                "name": "Data Analysis Agent",
                "desc": "Natural language → SQL → execute → insight",
            },
            {
                "key": "research",
                "name": "Multi-Agent Research",
                "desc": "Plan → parallel research → synthesize",
            },
            {
                "key": "cost",
                "name": "Cost-Optimized Agent",
                "desc": "Model routing + semantic cache + cost metering",
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
        yield error(f"Unknown mode: {req.mode}").to_sse()
        yield done().to_sse()
        return

    try:
        async for ev in handler(req.message, req.history):
            yield ev.to_sse()
    except Exception as e:  # noqa: BLE001 - demo 中统一兜底，避免裸 500
        yield error(f"Execution error: {e}").to_sse()
        yield done().to_sse()


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    """统一的流式对话接口。

    多层防护：
    1. 语言检测 — 中文消息封禁
    2. 滥用检测 — 规则引擎 + LLM 终审识别测试流量
    3. 通过检测后正常处理

    返回 text/event-stream，前端用 EventSource 或 fetch+ReadableStream 消费。
    关闭 Nginx 缓冲（X-Accel-Buffering）以保证流式实时性。
    """
    ip = _get_client_ip(request)

    # 第一层：语言检测
    blocked, reason = await check_language_and_block(ip, req.message)
    if blocked:
        return JSONResponse(
            status_code=403,
            content={"detail": f"Access denied: {reason}"}
        )

    # 第二层：滥用检测
    is_abuse, abuse_reason = await detector.check_message(ip, req.message)
    if is_abuse:
        logger.warning("🚫 滥用检测封禁 IP=%s  原因=%s", ip, abuse_reason)
        await guard.manual_block(ip, reason=abuse_reason)
        return JSONResponse(
            status_code=403,
            content={"detail": f"Access denied: {abuse_reason}"}
        )

    return StreamingResponse(
        _event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
