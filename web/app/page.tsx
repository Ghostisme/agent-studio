"use client";

/**
 * 主页面：Agent Studio 的单页应用。
 *
 * 布局：顶部标题 + 模式切换 Tab，主体左右分栏——
 * 左侧对话面板，右侧实时执行链路图。
 * 这个「对话 + 执行可视化」并排的布局是本 demo 的核心卖点，
 * 让客户一眼看到「这个人能把 Agent 执行做成可观测的产品」。
 */

import { useAgentStore } from "@/lib/store";
import ChatPanel from "@/components/ChatPanel";
import ExecutionGraph from "@/components/ExecutionGraph";

/** 三种模式的展示信息。与后端 /api/modes 对齐，这里内联以避免首屏额外请求。 */
const MODES = [
  { key: "support", name: "Customer Support", desc: "RAG + Tool Calling" },
  { key: "data", name: "Data Analysis", desc: "NL → SQL → Insight" },
  { key: "research", name: "Multi-Agent Research", desc: "Plan → Parallel → Synthesize" },
];

export default function Home() {
  const { mode, nodes, setMode } = useAgentStore();

  return (
    <main className="flex h-screen flex-col bg-zinc-50 dark:bg-zinc-950">
      {/* 顶栏 */}
      <header className="border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
              Agent Studio
            </h1>
            <p className="text-xs text-zinc-500">
              LangGraph · Next.js · Streaming — production-grade agent patterns
            </p>
          </div>
        </div>

        {/* 模式切换 Tab */}
        <div className="mt-4 flex gap-2">
          {MODES.map((m) => (
            <button
              key={m.key}
              onClick={() => setMode(m.key)}
              className={`rounded-lg border px-4 py-2 text-left transition-colors ${
                mode === m.key
                  ? "border-emerald-400 bg-emerald-50 dark:bg-emerald-950/40"
                  : "border-zinc-200 bg-white hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900"
              }`}
            >
              <div className="text-sm font-medium text-zinc-800 dark:text-zinc-100">{m.name}</div>
              <div className="text-xs text-zinc-500">{m.desc}</div>
            </button>
          ))}
        </div>
      </header>

      {/* 主体：左对话 右执行图 */}
      <div className="grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-2">
        <section className="border-r border-zinc-200 dark:border-zinc-800">
          <ChatPanel />
        </section>
        <section className="hidden bg-white dark:bg-zinc-900 lg:block">
          <ExecutionGraph nodes={nodes} />
        </section>
      </div>
    </main>
  );
}
