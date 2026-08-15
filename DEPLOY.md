# 部署说明（Deployment）

这个 demo 是前后端分离结构，推荐分开部署：前端上 **Vercel**（Next.js 亲儿子，免费、快），后端上 **Railway** 或 **Render**（Python 服务，有免费额度）。

---

## 前端 → Vercel

1. 把整个 `agent-studio` 仓库 push 到 GitHub。
2. 在 [vercel.com](https://vercel.com) 点 **New Project**，导入这个仓库。
3. **Root Directory** 设为 `web`（重要：仓库根不是前端根）。
4. 环境变量加一条：
   - `NEXT_PUBLIC_API_BASE` = 后端的公网地址（部署完后端再回填）。
5. Deploy。

---

## 后端 → Railway

1. 在 [railway.app](https://railway.app) 点 **New Project → Deploy from GitHub repo**。
2. 选这个仓库，**Root Directory** 设为 `server`。
3. Railway 会自动识别 Python。设置启动命令：
   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
4. 环境变量：
   - `OPENAI_API_KEY` = 你的真实 key
   - `OPENAI_MODEL` = `gpt-4o-mini`（便宜，demo 够用）
   - `CORS_ORIGINS` = 你的 Vercel 前端地址（如 `https://agent-studio.vercel.app`）
5. 部署后拿到公网地址，回填到 Vercel 的 `NEXT_PUBLIC_API_BASE`，前端重新部署一次。

---

## 演示成本控制

- 用 `gpt-4o-mini`，三个模式跑一遍大约几分钱。
- 如果做成公开可访问的 demo，建议在后端加个简单限流（每 IP 每分钟 N 次），避免被刷爆 API 账单。生产化的话这是必做项。

---

## 录 Demo GIF（放进 README 和 Portfolio）

Portfolio 里一张会动的 GIF 胜过千言万语。录制建议：

1. 本地把前后端跑起来，填好真实 key。
2. 用录屏工具（Windows 自带 Xbox Game Bar，或 ScreenToGif）录一段 15-30 秒的操作：
   - 切到 **Multi-Agent Research** 模式（最能体现能力）
   - 输入一个研究主题
   - 让观众看到：planner 拆解 → 多个 researcher 节点并行点亮 → synthesizer 汇总，右侧执行图实时变化
3. 导出成 GIF，放到 README 顶部：`![demo](docs/demo.gif)`
4. 同一段也可以传到 Upwork Portfolio 作为封面。
