import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Agent Studio — LangGraph · Next.js Agent Patterns",
  description:
    "A production-grade demo of three AI agent patterns: RAG customer support, NL-to-SQL data analysis, and multi-agent research — with live execution visualization.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning：忽略 <html> 层的服务端/客户端属性差异。
    // 浏览器翻译插件（如 Trancy）会往 <html> 注入 trancy-version 之类的
    // 属性，导致 hydration 不匹配。这是第三方注入，非应用逻辑问题，
    // 官方推荐在此处抑制告警——不影响功能，也不会掩盖真正的 hydration bug
    // （该标志只作用于 <html> 这一层）。
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
