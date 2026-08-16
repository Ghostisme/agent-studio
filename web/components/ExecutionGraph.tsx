"use client";

/**
 * 执行链路图组件。
 *
 * 用 React Flow 把当前这轮 Agent 的执行过程可视化成一张流程图：
 * 每个节点是一个 Agent 步骤（规划/研究/汇总等），运行中的节点高亮，
 * 完成的置灰，节点下方列出它调用过的工具。
 *
 * 这是整个 demo 最有辨识度的部分——把「Agent 在干什么」变成看得见的图，
 * 直接呼应简历里「把 Agent 执行过程转化为清晰、可控、可追踪的产品体验」。
 */

import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  type Edge,
  Handle,
  type Node,
  Position,
} from "reactflow";
import "reactflow/dist/style.css";
import type { ExecNode } from "@/lib/types";

/** 自定义节点渲染：展示节点名、运行状态、工具调用列表。 */
function AgentNode({ data }: { data: ExecNode }) {
  const running = data.status === "running";
  return (
    <div
      className={`min-w-[180px] rounded-lg border-2 px-3 py-2 shadow-sm transition-colors ${
        running
          ? "border-emerald-400 bg-emerald-50 dark:bg-emerald-950/40"
          : "border-zinc-300 bg-white dark:border-zinc-700 dark:bg-zinc-900"
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-zinc-400" />
      <div className="flex items-center gap-2">
        {/* 运行中显示脉冲圆点，完成显示对勾，给用户明确的状态反馈 */}
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            running ? "animate-pulse bg-emerald-500" : "bg-zinc-400"
          }`}
        />
        <span className="text-sm font-medium text-zinc-800 dark:text-zinc-100">{data.label}</span>
      </div>
      {data.tools.length > 0 && (
        <div className="mt-2 space-y-1">
          {data.tools.map((t, i) => (
            <div
              key={i}
              className="rounded bg-zinc-100 px-2 py-1 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
            >
              🔧 {t.name}
              {t.result !== undefined && (
                <span className="ml-1 text-emerald-600 dark:text-emerald-400">✓</span>
              )}
            </div>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-zinc-400" />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

/**
 * 执行图容器。把 store 里的 ExecNode[] 转成 React Flow 的 nodes/edges。
 *
 * @param nodes - 当前这轮的执行节点（来自 Zustand store）
 */
export default function ExecutionGraph({ nodes }: { nodes: ExecNode[] }) {
  // 把线性/并行的节点摆成自上而下的布局。
  // researcher_N 这类并行节点排在同一行，其余节点竖直排列。
  const { flowNodes, flowEdges } = useMemo(() => {
    const flowNodes: Node[] = [];
    const flowEdges: Edge[] = [];

    // 按「是否并行研究节点」分组，实现并行节点横向铺开的布局。
    let row = 0;
    let i = 0;
    while (i < nodes.length) {
      const cur = nodes[i];
      // 收集连续的 researcher_* 节点作为并行组，同一行横向排列。
      if (cur.id.startsWith("researcher_")) {
        const group: ExecNode[] = [];
        while (i < nodes.length && nodes[i].id.startsWith("researcher_")) {
          group.push(nodes[i]);
          i++;
        }
        group.forEach((n, gi) => {
          flowNodes.push({
            id: n.id,
            type: "agent",
            position: { x: gi * 240 - (group.length - 1) * 120, y: row * 160 },
            data: n,
          });
        });
        row++;
      } else {
        flowNodes.push({
          id: cur.id,
          type: "agent",
          position: { x: 0, y: row * 160 },
          data: cur,
        });
        row++;
        i++;
      }
    }

    // 连边：按出现顺序把相邻节点串起来（并行节点都连向上一个和下一个）。
    for (let k = 1; k < nodes.length; k++) {
      const prev = nodes[k - 1];
      const cur = nodes[k];
      flowEdges.push({
        id: `${prev.id}->${cur.id}`,
        source: prev.id,
        target: cur.id,
        animated: cur.status === "running",
      });
    }

    return { flowNodes, flowEdges };
  }, [nodes]);

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-zinc-400">
        The agent&apos;s execution graph will appear here in real time
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.3 }}
      proOptions={{ hideAttribution: true }}
    >
      <Background />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
