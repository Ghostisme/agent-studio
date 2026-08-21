/**
 * SSE 流式请求客户端。
 *
 * 用 fetch + ReadableStream 消费后端的 text/event-stream。
 * 之所以不用原生 EventSource，是因为 EventSource 只支持 GET，
 * 而我们的对话接口需要 POST 传 message/history，所以手写解析。
 */

import type { StreamEvent } from "./types";

/** 后端服务地址。生产部署时通过环境变量注入。 */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "https://agent-studio-backend-one.vercel.app";

/**
 * 发起流式对话，逐条回调解析出的事件。
 *
 * @param params.mode - Agent 模式
 * @param params.message - 用户输入
 * @param params.history - 历史对话
 * @param onEvent - 每收到一条事件的回调
 * @param signal - 用于中断请求的 AbortSignal
 */
export async function streamChat(
  params: { mode: string; message: string; history: { role: string; content: string }[] },
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    signal,
  });

  if (!resp.body) throw new Error("Response has no data stream");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // SSE 以 "\n\n" 分隔每条消息。这里做增量解析：把收到的字节拼进
  // buffer，按分隔符切出完整消息，剩余不完整的留到下一轮。
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? ""; // 最后一段可能不完整，留到下次

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const json = line.slice(5).trim();
      if (!json) continue;
      try {
        onEvent(JSON.parse(json) as StreamEvent);
      } catch {
        // 单条解析失败不应中断整个流，跳过即可。
      }
    }
  }
}

/** 拉取可用模式列表。 */
export async function fetchModes(): Promise<{ modes: import("./types").AgentMode[] }> {
  const resp = await fetch(`${API_BASE}/api/modes`);
  return resp.json();
}
