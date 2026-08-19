/**
 * Agent 状态中心（Zustand）。
 *
 * 这是前端的核心：把后端推来的原始事件流「归约」成两个视图模型——
 * 1. messages：对话气泡（用户/助手）
 * 2. nodes：执行链路图的节点及其工具调用
 *
 * 设计意图：视图组件只订阅这里的派生状态，不直接碰事件流。
 * 事件 → 状态的所有逻辑收敛在 applyEvent 一处，便于维护和调试。
 * 这正是「基于 Zustand 建设 Agent 状态中心」在 demo 里的落地。
 */

import { create } from "zustand";
import { streamChat } from "./api";
import type { ExecNode, StreamEvent } from "./types";

/** 一条对话消息。 */
export interface Message {
  role: "user" | "assistant";
  content: string;
}

interface AgentState {
  mode: string; // 当前 Agent 模式
  messages: Message[]; // 对话历史
  nodes: ExecNode[]; // 当前这轮的执行图节点
  running: boolean; // 是否正在流式执行
  error: string | null;
  /** 余额不足时置 true，触发右上角 toast 并禁用发送按钮。 */
  quotaExceeded: boolean;

  setMode: (mode: string) => void;
  send: (text: string) => Promise<void>;
  reset: () => void;
  dismissQuotaToast: () => void;
}

export const useAgentStore = create<AgentState>((set, get) => ({
  mode: "support",
  messages: [],
  nodes: [],
  running: false,
  error: null,
  quotaExceeded: false,

  setMode: (mode) => set({ mode, messages: [], nodes: [], error: null }),

  reset: () => set({ messages: [], nodes: [], error: null }),

  dismissQuotaToast: () => set({ quotaExceeded: false }),

  /**
   * 发送一条消息并流式接收 Agent 执行过程。
   *
   * 每次发送会清空上一轮的执行图（nodes），因为执行图展示的是
   * 「当前这轮 Agent 怎么运作」，而对话历史（messages）保留累积。
   */
  send: async (text: string) => {
    const { mode, messages } = get();

    // 先把用户消息落地，并追加一个空的助手气泡用于承接流式文本。
    set({
      running: true,
      error: null,
      nodes: [],
      messages: [...messages, { role: "user", content: text }, { role: "assistant", content: "" }],
    });

    // 传给后端的 history 不含刚加的空助手气泡。
    const history = messages.map((m) => ({ role: m.role, content: m.content }));

    try {
      await streamChat({ mode, message: text, history }, (ev) => applyEvent(set, get, ev));
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      set({ running: false });
    }
  },
}));

/**
 * 事件归约器：把单条事件应用到 store 状态上。
 *
 * 抽成独立函数而非塞进 send 内部，是为了让「事件如何改变状态」
 * 这段核心逻辑清晰、可单测、可扩展新事件类型。
 */
function applyEvent(
  set: (fn: (s: AgentState) => Partial<AgentState>) => void,
  get: () => AgentState,
  ev: StreamEvent,
): void {
  const node = ev.node ?? "";

  switch (ev.type) {
    case "node_start":
      // 新节点开始，加入执行图并标记 running。
      set((s) => ({
        nodes: [
          ...s.nodes,
          { id: node, label: ev.data?.label ?? node, status: "running", tools: [] },
        ],
      }));
      break;

    case "node_end":
      set((s) => ({
        nodes: s.nodes.map((n) => (n.id === node ? { ...n, status: "done" } : n)),
      }));
      break;

    case "token": {
      // 流式文本片段：追加到最后一个助手气泡。
      const text = ev.data?.text ?? "";
      set((s) => {
        const msgs = [...s.messages];
        const last = msgs[msgs.length - 1];
        if (last?.role === "assistant") last.content += text;
        return { messages: msgs };
      });
      break;
    }

    case "tool_call":
      // 工具调用：挂到对应节点下。
      set((s) => ({
        nodes: s.nodes.map((n) =>
          n.id === node
            ? { ...n, tools: [...n.tools, { name: ev.data?.name ?? "", args: ev.data?.args }] }
            : n,
        ),
      }));
      break;

    case "tool_result":
      // 工具结果：回填到该节点最近一次同名工具调用上。
      set((s) => ({
        nodes: s.nodes.map((n) => {
          if (n.id !== node) return n;
          const tools = [...n.tools];
          // 从后往前找第一个匹配名字且还没结果的调用回填。
          for (let i = tools.length - 1; i >= 0; i--) {
            if (tools[i].name === ev.data?.name && tools[i].result === undefined) {
              tools[i] = { ...tools[i], result: ev.data?.result };
              break;
            }
          }
          return { ...n, tools };
        }),
      }));
      break;

    case "final": {
      // 最终答案：确保助手气泡内容完整（token 累积可能与 final 一致，
      // 但 data 模式最终文本来自 final，这里以 final 为准兜底）。
      const text = ev.data?.text ?? "";
      set((s) => {
        const msgs = [...s.messages];
        const last = msgs[msgs.length - 1];
        if (last?.role === "assistant" && !last.content) last.content = text;
        return { messages: msgs };
      });
      break;
    }

    case "error": {
      const msg = ev.data?.message ?? "Unknown error";
      // quota 错误单独处理：设 quotaExceeded 标志，不写入 error 字段，
      // 避免在对话气泡里直接显示含中文的原始报错。
      if (msg.includes("pre_consume_token_quota_failed") || msg.includes("配额不足")) {
        set(() => ({ quotaExceeded: true }));
      } else {
        set(() => ({ error: msg }));
      }
      break;
    }

    case "done":
      // 流结束，running 态由 send 的 finally 统一收尾，这里无需处理。
      break;
  }
}
