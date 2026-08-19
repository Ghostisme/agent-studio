# Agent Studio

[![Next.js](https://img.shields.io/badge/Next.js-16-black?logo=next.js)](https://nextjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-1C3C3C?logo=langchain)](https://langchain-ai.github.io/langgraph)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://python.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://typescriptlang.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **AI Agent Platform** — three production agent patterns built with LangGraph + Next.js, with live execution visualization.
>
> **[→ Try the live demo](https://agent-studio-rho.vercel.app)**

[**Live Demo**](https://agent-studio-rho.vercel.app) · [**Deploy Guide**](DEPLOY.md)

![Agent Studio demo](docs/demo.gif)

---

## What this solves

Most "AI agent" integrations stop at wrapping a chat completion call. What actually matters in production is **making execution observable, traceable, and controllable** — so you can debug, trust, and iterate on it.

Agent Studio is a full-stack platform that shows exactly that: planning, tool calls, parallel agent execution, and streaming — all rendered live in the UI as structured events.

Three agent patterns, one shared streaming protocol:

| Mode | Pattern | What it demonstrates |
|------|---------|----------------------|
| 🎧 **Customer Support** | RAG + Tool Calling (ReAct) | Knowledge retrieval + real-time tool invocation |
| 📊 **Data Analysis** | NL → SQL → Execute → Insight | Safe code generation with guardrails |
| 🔬 **Multi-Agent Research** | Plan → Parallel Research → Synthesize | LangGraph orchestration & parallel agents |

---

## Business applications

This architecture directly applies if you're building:

- **Customer support automation** — an AI bot that queries your knowledge base and calls your business APIs (order status, inventory, CRM)
- **Data products** — natural language interfaces over your database, with generated SQL, execution, and insight summarization
- **Research / analysis tools** — multi-agent workflows that break down a task, research in parallel, and synthesize a structured report
- **Any AI feature where observability matters** — the event streaming protocol works for any agent pattern, not just these three

> The platform is designed to be extended: adding a new agent mode means writing one handler and registering it in the route table. No frontend change needed.

---

## Engineering highlights

Four decisions that separate this from a toy implementation:

1. **Protocol-first.** The SSE event schema ([`events.py`](server/app/events.py) ↔ [`types.ts`](web/lib/types.ts)) was designed before either side was built. That's why one frontend renders three very different agent flows without mode-specific code.

2. **Real parallel execution.** The research agent runs sub-tasks concurrently with `asyncio.gather` and streams results as each completes (`as_completed`) — the UI lights up multiple researcher nodes at once.

3. **Failure as structured state.** Every stream terminates with either `final` or `error`, always followed by `done`. The UI never hangs on a failed agent call.

4. **Honest mock layer.** RAG uses keyword matching; the DB is in-memory SQLite. Every mock is commented with what it replaces in production (vector search, a real data warehouse). Nothing pretends to be more than it is.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  Next.js (App Router) — web/                   │
│  • Streaming chat UI                           │
│  • Live execution graph (React Flow)           │
│  • Zustand agent state center                  │
└───────────────────┬────────────────────────────┘
                    │  SSE (text/event-stream)
                    │  shared event protocol
┌───────────────────▼────────────────────────────┐
│  FastAPI + LangGraph — server/                  │
│  ┌────────────────────────────────────────────┐ │
│  │ support_agent   RAG + ReAct tool calling    │ │
│  │ data_agent      NL→SQL→execute→insight      │ │
│  │ research_agent  StateGraph multi-agent orch.│ │
│  └────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

**Layered, decoupled, extensible.** The shared protocol is the key: the frontend renders any agent mode through a single event-driven state machine. Adding a fourth agent requires zero frontend changes.

---

## Tech stack

**Frontend:** Next.js 16 (App Router) · React 19 · TypeScript · Zustand · React Flow · Tailwind CSS  
**Backend:** Python 3.12 · FastAPI · LangGraph · LangChain  
**Streaming:** Server-Sent Events with a custom structured event protocol

---

## Quick start

### 1. Backend

```bash
cd server
python -m venv .venv
source .venv/Scripts/activate      # Windows
# source .venv/bin/activate        # macOS/Linux
pip install -r requirements.txt

cp .env.example .env                # then add your OPENAI_API_KEY
uvicorn app.main:app --reload --port 8000
```

> Works with any OpenAI-compatible endpoint — set `OPENAI_BASE_URL` to point at your own gateway.

### 2. Frontend

```bash
cd web
pnpm install
cp .env.local.example .env.local
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000).

---

## Project structure

```
agent-studio/
├── server/
│   └── app/
│       ├── main.py          # FastAPI entry, unified /api/chat streaming endpoint
│       ├── events.py        # Shared SSE event protocol (the core contract)
│       ├── llm.py           # Swappable LLM provider wrapper
│       └── modes/
│           ├── support_agent.py    # Mode 1: RAG + tool calling
│           ├── data_agent.py       # Mode 2: NL → SQL → insight
│           └── research_agent.py  # Mode 3: multi-agent orchestration
└── web/
    ├── app/page.tsx         # Single-page layout: chat + execution graph
    ├── components/
    │   ├── ChatPanel.tsx           # Conversation UI
    │   └── ExecutionGraph.tsx      # Live agent execution graph (React Flow)
    └── lib/
        ├── types.ts         # Event types mirrored from the backend
        ├── api.ts           # SSE streaming client
        └── store.ts         # Zustand state center (event → view-model reducer)
```

---

## Deployment

See [DEPLOY.md](DEPLOY.md) for step-by-step instructions to deploy the frontend to Vercel and the backend to Railway.

---

## License

MIT
