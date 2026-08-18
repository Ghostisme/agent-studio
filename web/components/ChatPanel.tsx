"use client";

/**
 * 对话面板组件。
 *
 * 负责展示对话气泡、输入框，以及针对不同模式的示例问题快捷入口。
 * 只从 store 读派生状态、只调用 store 的 send，本身不含业务逻辑，
 * 保持展示组件的纯粹。
 */

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAgentStore } from "@/lib/store";

/** 各模式的示例问题，降低用户上手成本，也方便你录 demo 时快速触发。 */
const EXAMPLES: Record<string, string[]> = {
  support: ["What is your return policy?", "Track my order SO202608001"],
  data: ["Which category has the highest total sales?", "How do sales break down by region?"],
  research: [
    "Current state of AI agents in enterprise customer service",
    "Key technical challenges in multi-agent systems",
  ],
};

export default function ChatPanel() {
  const { mode, messages, running, error, send } = useAgentStore();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新消息到达时自动滚到底部，保证最新内容始终可见。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const handleSend = (text: string) => {
    if (!text.trim() || running) return;
    setInput("");
    void send(text);
  };

  return (
    <div className="flex h-full flex-col">
      {/* 对话区 */}
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="space-y-3 pt-8 text-center">
            <p className="text-sm text-zinc-400">Try one of these:</p>
            {EXAMPLES[mode]?.map((q) => (
              <button
                key={q}
                onClick={() => handleSend(q)}
                className="mx-auto block rounded-full border border-zinc-300 px-4 py-1.5 text-sm text-zinc-600 transition-colors hover:border-emerald-400 hover:text-emerald-600 dark:border-zinc-700 dark:text-zinc-300"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            {m.role === "user" ? (
              // 用户消息：简单气泡，保留换行即可
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-emerald-500 px-4 py-2.5 text-sm text-white">
                {m.content}
              </div>
            ) : (
              // 助手消息：渲染 Markdown，气泡宽度放宽到 92% 以容纳长内容
              <div className="markdown-bubble max-w-[92%] rounded-2xl bg-zinc-100 px-4 py-3 text-sm text-zinc-800 dark:bg-zinc-800 dark:text-zinc-100">
                {m.content ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                ) : running && i === messages.length - 1 ? (
                  <span className="animate-pulse text-zinc-400">Thinking…</span>
                ) : null}
              </div>
            )}
          </div>
        ))}

        {error && (
          <div className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600 dark:bg-red-950/40">
            ⚠️ {error}
          </div>
        )}
      </div>

      {/* 输入区 */}
      <div className="border-t border-zinc-200 p-3 dark:border-zinc-800">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend(input)}
            placeholder={running ? "Agent is running…" : "Ask a question…"}
            disabled={running}
            className="flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-400 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          />
          <button
            onClick={() => handleSend(input)}
            disabled={running || !input.trim()}
            className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-600 disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
