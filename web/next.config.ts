import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 关闭 Next.js 自动生成 AGENTS.md / CLAUDE.md（给 AI 工具看的文件）。
  // 这是 Portfolio 仓库，不需要这些噪音文件反复出现在工作区。
  agentRules: false,
};

export default nextConfig;
