/**
 * 流式事件类型定义。
 *
 * 与后端 server/app/events.py 的协议严格对齐——这是前后端的共享契约。
 * 后端把 LangGraph 执行过程编码成这些事件通过 SSE 推来，前端据此
 * 更新对话内容和执行链路图。任何一端改协议，另一端必须同步。
 */

/** 事件类型，与后端 EventType 枚举一一对应。 */
export type EventType =
  | "node_start"
  | "node_end"
  | "token"
  | "tool_call"
  | "tool_result"
  | "final"
  | "error"
  | "done";

/** 单条流式事件。data 的具体字段随 type 变化（见后端各工厂函数）。 */
export interface StreamEvent {
  type: EventType;
  node?: string;
  data?: {
    label?: string; // node_start：节点的可读名称
    text?: string; // token / final：文本内容
    name?: string; // tool_call / tool_result：工具名
    args?: Record<string, unknown>; // tool_call：工具参数
    result?: string; // tool_result：工具返回
    message?: string; // error：错误信息
  };
}

/** Agent 模式的元信息，用于渲染模式切换 Tab。 */
export interface AgentMode {
  key: "support" | "data" | "research";
  name: string;
  desc: string;
}

/** 执行图中单个节点的运行态。 */
export type NodeStatus = "running" | "done";

/** 执行图节点，供 React Flow 渲染。 */
export interface ExecNode {
  id: string; // 对应后端事件的 node 字段
  label: string; // 可读名称
  status: NodeStatus;
  tools: { name: string; args?: unknown; result?: string }[]; // 该节点上的工具调用
}
