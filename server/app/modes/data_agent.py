"""模式二：数据分析 Agent（自然语言 → SQL → 执行 → 洞察）。

场景：用户用自然语言问数据问题（如「哪个品类销量最高」），
Agent 生成 SQL、在内置数据集上执行、把结果转成自然语言洞察。

这个模式专门展示「代码/查询生成 + 安全执行 + 结果解读」的链路，
是数据类 AI 产品的核心能力，也是溢价较高的方向。

安全设计：只允许 SELECT 查询（拒绝写操作），在内存 SQLite 上执行，
避免生成的 SQL 造成破坏——生产环境同样需要这层护栏，此处直接体现。
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import AsyncIterator

from ..events import StreamEvent, done, final, node_end, node_start, token, tool_call, tool_result
from ..llm import get_llm

# ── 内置演示数据集 ────────────────────────────────────────────
# 一张简单的销售表，够展示分组、聚合、排序等典型分析查询。
# 真实项目会连客户的数仓（这里用内存 SQLite 免依赖、可一键跑）。
_SCHEMA = """CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    category TEXT,      -- 品类
    product TEXT,       -- 商品名
    region TEXT,        -- 销售区域
    amount REAL,        -- 销售额
    quantity INTEGER,   -- 销量
    order_date TEXT     -- 下单日期 YYYY-MM-DD
);"""

_SEED = [
    (1, "数码", "无线耳机", "华东", 12800.0, 40, "2026-07-02"),
    (2, "数码", "机械键盘", "华南", 8600.0, 20, "2026-07-05"),
    (3, "家居", "记忆棉枕", "华东", 4200.0, 60, "2026-07-08"),
    (4, "家居", "香薰机", "华北", 3100.0, 50, "2026-07-11"),
    (5, "服饰", "运动卫衣", "华南", 9900.0, 90, "2026-07-15"),
    (6, "数码", "无线耳机", "华北", 15600.0, 48, "2026-07-18"),
    (7, "服饰", "牛仔裤", "华东", 7300.0, 70, "2026-07-21"),
    (8, "家居", "记忆棉枕", "华南", 5600.0, 80, "2026-07-25"),
]


def _build_db() -> sqlite3.Connection:
    """构建内存数据库并灌入演示数据。每次请求新建，保证隔离、无副作用。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(_SCHEMA)
    conn.executemany("INSERT INTO sales VALUES (?,?,?,?,?,?,?)", _SEED)
    conn.commit()
    return conn


def _is_safe_select(sql: str) -> bool:
    """校验 SQL 只读。

    只允许单条 SELECT，拒绝任何写/DDL 关键字。这是防止 LLM 生成
    危险语句的护栏——生产环境同样必须有，不能信任模型输出直接执行。
    """
    normalized = sql.strip().rstrip(";").lower()
    if not normalized.startswith("select"):
        return False
    forbidden = ["insert", "update", "delete", "drop", "alter", "create", "attach", "pragma"]
    return not any(re.search(rf"\b{kw}\b", normalized) for kw in forbidden)


def _run_sql(sql: str) -> str:
    """在演示数据库上执行只读 SQL，返回格式化的结果表。"""
    if not _is_safe_select(sql):
        return "拒绝执行：仅允许只读 SELECT 查询。"
    conn = _build_db()
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if not rows:
            return "查询无结果。"
        # 拼成简单文本表格，交给 LLM 解读。
        lines = [" | ".join(cols)]
        lines += [" | ".join(str(c) for c in row) for row in rows]
        return "\n".join(lines)
    except sqlite3.Error as e:
        # 把 SQL 错误回传给 Agent，让它有机会修正后重试。
        return f"SQL 执行错误：{e}"
    finally:
        conn.close()


_SQL_PROMPT = f"""你是数据分析助手。数据库表结构如下：
{_SCHEMA}

用户会用自然语言提问。你只需输出一条可执行的 SQLite SELECT 查询语句，
不要输出任何解释、不要用 markdown 代码块包裹，直接输出 SQL。"""


async def run(message: str, history: list[dict]) -> AsyncIterator[StreamEvent]:
    """运行数据分析 Agent。

    流程分三个显式节点，方便前端在执行图上展示：
    1. sql_gen：LLM 把自然语言翻译成 SQL
    2. execute：安全执行 SQL
    3. insight：LLM 根据结果生成自然语言洞察

    Args:
        message: 用户的数据问题。
        history: 历史对话（本模式主要用当前问题，history 备用）。

    Yields:
        StreamEvent: 三个节点的执行事件。
    """
    llm = get_llm(streaming=False, temperature=0.0)  # 生成 SQL 要确定性，温度设 0

    # ── 节点 1：生成 SQL ──
    yield node_start("sql_gen", "生成 SQL 查询")
    sql_resp = await llm.ainvoke([("system", _SQL_PROMPT), ("user", message)])
    sql = sql_resp.content.strip()
    # 去掉可能残留的 markdown 代码块围栏。
    sql = re.sub(r"^```(?:sql)?|```$", "", sql, flags=re.MULTILINE).strip()
    yield token("sql_gen", sql)
    yield node_end("sql_gen")

    # ── 节点 2：执行 SQL ──
    yield node_start("execute", "执行查询")
    yield tool_call("execute", "run_sql", {"sql": sql})
    result = _run_sql(sql)
    yield tool_result("execute", "run_sql", result)
    yield node_end("execute")

    # ── 节点 3：解读结果 ──
    yield node_start("insight", "生成分析洞察")
    insight_llm = get_llm(streaming=True, temperature=0.3)
    insight_prompt = (
        f"用户问题：{message}\n\n查询结果：\n{result}\n\n"
        "请用简洁的中文总结数据洞察，指出关键结论。"
    )
    final_text = ""
    async for chunk in insight_llm.astream([("user", insight_prompt)]):
        text = chunk.content if isinstance(chunk.content, str) else ""
        if text:
            final_text += text
            yield token("insight", text)
    yield node_end("insight")

    yield final("insight", final_text)
    yield done()
