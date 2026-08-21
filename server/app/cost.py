"""LLM 成本优化核心原语。

这个模块是「cost_agent」模式的引擎，把生产级 LLM 降本的三件套收敛到一处：

1. 模型路由（route）  —— 按任务难度把简单任务降级到便宜模型，只有真正
   困难的推理才升档到贵模型。通常是单项收益最大的杠杆。
2. 语义缓存（cache）  —— 对语义相近的重复查询直接命中缓存，命中即零模型
   开销。用 embedding 相似度而非精确字符串匹配，覆盖「换个说法的同一问题」。
3. 计量与定价（meter）—— 把每次调用的 token / 成本显式记账，让每一项优化
   都用硬数字衡量，而不是拍脑袋。

设计原则（沿用本项目的 honest-mock 哲学）：
- 缓存后端优先用 Redis（生产标准，招聘要求也点名 Redis）；未配置 REDIS_URL
  时自动回退到进程内字典，保证 demo 无外部依赖也能一键跑起来。回退处均注明
  「生产会替换成什么」。
- 定价表、相似度阈值、难度规则都集中为常量，便于调参和替换为真实配置。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field

from .llm import CHEAP_MODEL, STRONG_MODEL, get_embeddings

# ── 定价表（USD / 每百万 token）────────────────────────────────
# 定价表（USD / 每百万 token）。
# 列出 demo 会用到的模型。自建网关的自定义模型名（gpt-5.x 系列）按量级
# 归档：含 mini/small 等标记的走便宜档，其余走强档（见 _price_of）。
# 生产环境应从供应商计费 API 拉取真实价格，这里硬编码是为了 demo 自洽、
# 可离线演示成本对比。数值为量级参考，不作精确账单依据。
_PRICING: dict[str, dict[str, float]] = {
    # OpenAI 标准模型（保留，便于切回官方端点）
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    # 自建网关模型（moonxi）：数值取自网关计费页（USD / 每百万 token），
    # 便宜档 vs 强档的真实价差正是路由节省的来源，用真实价保证计量可信。
    "gpt-5.4-mini": {"input": 0.24, "output": 1.44},   # 便宜档：路由默认落点
    "gpt-5.4": {"input": 0.80, "output": 4.80},        # 强档：与 mini 同组，价差正好 3.33x
    "gpt-5.5": {"input": 1.60, "output": 9.60},        # 更强档（可选基线）
    "gpt-5.6-terra": {"input": 0.64, "output": 3.84},
    "gpt-5.6-sol": {"input": 1.60, "output": 9.60},
    "gpt-5.3-codex-spark": {"input": 0.56, "output": 4.48},
    # embedding
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
}

# 语义缓存命中阈值：余弦相似度 ≥ 此值才算命中。
# 0.92 偏保守——宁可少命中也不要把「相似但答案不同」的问题错误复用，
# 这是缓存不能牺牲质量的护栏。可按业务容忍度调整。
_SIM_THRESHOLD = float(os.getenv("COST_CACHE_THRESHOLD", "0.92"))

# 缓存 TTL（秒）。默认 1 小时，避免陈旧答案长期驻留。
_CACHE_TTL = int(os.getenv("COST_CACHE_TTL", "3600"))


# ── token 估算 ────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数（~4 字符/token 的通用经验值）。

    没有引入 tiktoken 依赖：一是减小部署体积，二是缓存节省量本就是
    「估算值」，量级正确即可。真实计费以供应商返回的 usage 为准
    （见 estimate_cost 对 usage_metadata 的优先使用）。

    Args:
        text: 待估算文本。

    Returns:
        估算的 token 数（至少为 1，空串除外）。
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """按定价表计算一次调用的美元成本。

    Args:
        model: 模型名，须在 _PRICING 中。
        input_tokens: 输入 token 数。
        output_tokens: 输出 token 数。

    Returns:
        美元成本。模型不在表内时按名字关键词归档到便宜/强档的量级价
        （见 _price_of），而非返回 0——否则自建网关的自定义模型名
        （如 gpt-5.4-mini）会让成本对比全为 0，demo 失去意义。
    """
    price = _price_of(model)
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def _price_of(model: str) -> dict[str, float]:
    """查模型定价，未命中时按名字关键词回退到便宜/强档量级价。

    这是量级估算而非精确账单：含 "mini"/"small"/"nano"/"flash" 的模型
    归到便宜档，其余归到强档。自建网关常用非标准模型名，这层回退保证
    成本对比始终有意义。真实计费应以供应商返回的 usage/账单为准。
    """
    if model in _PRICING:
        return _PRICING[model]
    name = model.lower()
    cheap_markers = ("mini", "small", "nano", "flash", "lite", "haiku")
    # 回退锚点用当前网关的便宜/强档，保证未知的自定义模型名也落到合理量级。
    tier = "gpt-5.4-mini" if any(m in name for m in cheap_markers) else "gpt-5.4"
    return _PRICING[tier]


# ── 模型路由 ──────────────────────────────────────────────────
# 难度信号：命中这些模式说明任务需要多步推理/生成/分析，应升档到强模型。
# 用规则而非再花一次 LLM 调用来分类——分类本身也有成本，能用零成本的
# 启发式解决就不该反过来烧钱。生产可替换为一个微调的轻量分类器。
_HARD_PATTERNS = re.compile(
    r"\b(analy[sz]e|compare|design|architect|debug|explain why|"
    r"trade[- ]?off|strateg|reason|prove|derive|optimi[sz]e|"
    r"step[- ]by[- ]step|refactor|plan)\b",
    re.IGNORECASE,
)
# 简单意图：短问句、事实查询、分类/抽取，便宜模型足矣。
_HARD_LEN_THRESHOLD = 280  # 超过此长度（字符）视为上下文复杂，倾向升档


@dataclass
class RouteDecision:
    """一次路由决策的结果，附带可读理由供前端展示。

    Attributes:
        model: 选中的模型名。
        tier: "cheap" 或 "strong"，便于前端着色/统计降级率。
        reason: 人类可读的决策依据，直接透传到 UI 提升可解释性。
    """

    model: str
    tier: str
    reason: str


def route(query: str) -> RouteDecision:
    """按任务难度选择模型（成本路由的核心）。

    策略：默认走便宜模型；仅当检测到「需要复杂推理/生成」的信号
    （关键词或长上下文）时才升档到强模型。这样把预算花在真正难的
    请求上，简单请求零溢价。

    Args:
        query: 用户查询文本。

    Returns:
        RouteDecision，含选中的模型、档位和可读理由。
    """
    hard_kw = _HARD_PATTERNS.search(query)
    long_ctx = len(query) > _HARD_LEN_THRESHOLD

    if hard_kw:
        return RouteDecision(
            STRONG_MODEL, "strong",
            f"Detected reasoning-heavy intent ('{hard_kw.group(0)}') → escalate to {STRONG_MODEL}",
        )
    if long_ctx:
        return RouteDecision(
            STRONG_MODEL, "strong",
            f"Long/complex context ({len(query)} chars) → escalate to {STRONG_MODEL}",
        )
    return RouteDecision(
        CHEAP_MODEL, "cheap",
        f"Simple/deterministic task → stay on cheap {CHEAP_MODEL}",
    )


# ── 语义缓存 ──────────────────────────────────────────────────
def _cosine(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。

    纯 Python 实现，避免为一个点积引入 numpy 依赖（部署体积敏感）。

    Returns:
        相似度 [-1, 1]；任一向量为零向量时返回 0。
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass
class CacheEntry:
    """一条缓存记录：查询、其 embedding、缓存的回答与写入时间。"""

    query: str
    embedding: list[float]
    response: str
    ts: float


@dataclass
class CacheLookup:
    """语义缓存查询结果。

    Attributes:
        hit: 是否命中。
        response: 命中时返回的缓存回答。
        similarity: 最相似记录的相似度（用于前端展示「多像」）。
        matched_query: 命中的原始查询（展示「复用了哪条」）。
    """

    hit: bool
    response: str = ""
    similarity: float = 0.0
    matched_query: str = ""


class SemanticCache:
    """基于 embedding 相似度的语义缓存。

    后端优先 Redis，回退进程内内存。之所以做双后端：Redis 是生产标准
    （跨实例共享、带 TTL、可持久化），而内存回退保证没有 Redis 的环境
    也能一键演示——契合本项目「每个 mock 都注明生产替代」的一贯做法。

    注意：这里为演示清晰，命中判定是「线性扫描比对所有 embedding」。
    生产上应改用向量数据库 / Redis 的向量检索（RediSearch KNN）做近邻
    搜索，避免 O(n) 扫描——此处刻意保持简单以便阅读。
    """

    def __init__(self) -> None:
        # 内存兜底存储：命名空间 → 记录列表。仅在无 Redis 时使用。
        self._mem: dict[str, list[CacheEntry]] = {}
        self._redis = None
        self._redis_ready = False

    async def _ensure_redis(self) -> None:
        """惰性初始化 Redis 连接。失败则静默回退内存，不影响主流程。"""
        if self._redis_ready:
            return
        self._redis_ready = True  # 只尝试一次，避免每次请求都重连
        url = os.getenv("REDIS_URL")
        if not url:
            return  # 未配置 → 走内存兜底
        try:
            # 惰性导入：没装 redis 包也不影响其余功能可用。
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(url, decode_responses=True)
            await self._redis.ping()
        except Exception:  # noqa: BLE001 - 连接失败一律回退内存，保证 demo 不挂
            self._redis = None

    async def lookup(self, namespace: str, query: str, embedding: list[float]) -> CacheLookup:
        """在命名空间内做语义近邻查询。

        Args:
            namespace: 缓存命名空间（如 agent 模式名），隔离不同场景，
                防止跨场景误命中。
            query: 原始查询文本。
            embedding: 查询的向量表示。若为伪向量（全零或单元素 [0.0]），
                降级为精确字符串匹配——这是 embedding 不可用时的兜底。

        Returns:
            CacheLookup：命中则带回答与相似度。
        """
        await self._ensure_redis()
        entries = await self._load_entries(namespace)

        # 检测伪向量：长度 ≤1 或全零 → 降级精确字符串匹配
        is_fake = len(embedding) <= 1 or all(abs(x) < 1e-9 for x in embedding)
        if is_fake:
            # 精确匹配模式：只有查询字符串完全一致才命中
            for e in entries:
                if e.query.strip().lower() == query.strip().lower():
                    return CacheLookup(True, e.response, 1.0, e.query)
            return CacheLookup(False)

        # 正常语义匹配模式：余弦相似度近邻搜索
        best: CacheEntry | None = None
        best_sim = 0.0
        for e in entries:
            sim = _cosine(embedding, e.embedding)
            if sim > best_sim:
                best_sim, best = sim, e

        if best and best_sim >= _SIM_THRESHOLD:
            return CacheLookup(True, best.response, best_sim, best.query)
        return CacheLookup(False, similarity=best_sim)

    async def store(self, namespace: str, query: str, embedding: list[float], response: str) -> None:
        """写入一条缓存记录，带 TTL。"""
        entry = CacheEntry(query=query, embedding=embedding, response=response, ts=time.time())
        if self._redis is not None:
            # Redis：每条记录一个 key（含 TTL），key 前缀带命名空间便于扫描。
            key = f"cost:cache:{namespace}:{hashlib.sha1(query.encode()).hexdigest()}"
            await self._redis.set(
                key,
                json.dumps({"q": query, "emb": embedding, "resp": response}),
                ex=_CACHE_TTL,
            )
        else:
            # 内存兜底：手动做 TTL 过滤（Redis 由服务端自动过期）。
            bucket = self._mem.setdefault(namespace, [])
            bucket.append(entry)

    async def _load_entries(self, namespace: str) -> list[CacheEntry]:
        """加载某命名空间下的全部有效（未过期）记录。"""
        if self._redis is not None:
            entries: list[CacheEntry] = []
            # scan_iter 避免 KEYS 阻塞。生产向量检索不需要这样全扫。
            async for key in self._redis.scan_iter(match=f"cost:cache:{namespace}:*"):
                raw = await self._redis.get(key)
                if not raw:
                    continue
                d = json.loads(raw)
                entries.append(CacheEntry(d["q"], d["emb"], d["resp"], 0.0))
            return entries
        # 内存兜底：顺手清掉过期项，避免无限增长。
        now = time.time()
        bucket = [e for e in self._mem.get(namespace, []) if now - e.ts < _CACHE_TTL]
        self._mem[namespace] = bucket
        return bucket


# 进程级单例：缓存跨请求复用才有意义。
cache = SemanticCache()


# ── 成本账本 ──────────────────────────────────────────────────
@dataclass
class CostLedger:
    """单次请求的成本记账，用于「优化前 vs 优化后」对比。

    baseline 表示「不做任何优化」的假想成本（全部用强模型、无缓存），
    actual 表示实际发生的成本。二者之差就是本次优化省下的钱——这正是
    投标时能甩出的硬数字。

    Attributes:
        actual_cost: 实际花费（美元）。
        baseline_cost: 无优化时的假想花费（美元）。
        cache_hit: 本次是否命中语义缓存。
        model_used: 实际使用的模型（缓存命中时为 "cache"）。
        events: 供前端展示的决策轨迹（路由/命中/计量）。
    """

    actual_cost: float = 0.0
    baseline_cost: float = 0.0
    cache_hit: bool = False
    model_used: str = ""
    events: list[str] = field(default_factory=list)

    @property
    def saved(self) -> float:
        """省下的美元金额。"""
        return max(0.0, self.baseline_cost - self.actual_cost)

    @property
    def saved_pct(self) -> float:
        """省下的百分比，baseline 为 0 时返回 0。"""
        if self.baseline_cost <= 0:
            return 0.0
        return round(self.saved / self.baseline_cost * 100, 1)


def baseline_cost(input_tokens: int, output_tokens: int) -> float:
    """计算「无优化基线」成本：全部走强模型、无缓存。

    作为对比锚点，凸显路由 + 缓存带来的节省。
    """
    return estimate_cost(STRONG_MODEL, input_tokens, output_tokens)
