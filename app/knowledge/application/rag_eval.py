"""RAG 离线评测 — 纯函数指标层。

这个模块负责：
- 定义评测用例结构与命中判定
- 计算 hit@k / MRR 等排序指标

这个模块不负责：
- 连接 Milvus 执行检索（见 scripts/rag_eval.py）
- 评测集内容维护（见 scripts/golden_set.jsonl）

WHY 需要评测：整条检索链路（书面化改写 → 混合检索 → RRF → rerank）此前
没有任何质量度量——rerank 开着但从未验证有收益，BM25 修复也拿不出数字。
有了 golden set，每次动检索参数都从"感觉变好了"变成 recall/MRR 的对比。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    """单条评测用例。

    命中判定二选一（可同时给，任一满足即命中）：
    - expect_doc_ids：命中结果的 doc_id 落在集合内
    - expect_any：命中结果的 raw_text 包含任一子串（大小写不敏感）
    """

    query: str
    expect_doc_ids: tuple[str, ...] = ()
    expect_any: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不能为空")
        if not self.expect_doc_ids and not self.expect_any:
            raise ValueError("必须提供 expect_doc_ids 或 expect_any 之一")


@dataclass(frozen=True)
class CaseResult:
    """单条用例的评测结果。"""

    query: str
    hit_rank: int | None  # 首个命中的名次（1 起）；None = 未命中
    top_k: int

    @property
    def hit(self) -> bool:
        return self.hit_rank is not None

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.hit_rank if self.hit_rank else 0.0


@dataclass(frozen=True)
class EvalReport:
    """整体评测报告。"""

    results: tuple[CaseResult, ...]
    top_k: int
    notes: tuple[str, ...] = field(default=())

    @property
    def case_count(self) -> int:
        return len(self.results)

    @property
    def hit_rate(self) -> float:
        """hit@k：前 k 条内命中的用例占比。"""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.hit) / len(self.results)

    @property
    def mrr(self) -> float:
        """Mean Reciprocal Rank：首个命中名次的倒数均值。"""
        if not self.results:
            return 0.0
        return sum(r.reciprocal_rank for r in self.results) / len(self.results)

    def summary_line(self) -> str:
        return (
            f"cases={self.case_count} top_k={self.top_k} "
            f"hit@{self.top_k}={self.hit_rate:.3f} mrr={self.mrr:.3f}"
        )


def matches_case(case: EvalCase, retrieved: dict[str, Any]) -> bool:
    """判定单条检索结果是否命中用例期望。"""
    doc_id = str(retrieved.get("doc_id") or "")
    if case.expect_doc_ids and doc_id in case.expect_doc_ids:
        return True
    if case.expect_any:
        text = str(retrieved.get("raw_text") or "").lower()
        return any(needle.lower() in text for needle in case.expect_any)
    return False


def evaluate_case(
    case: EvalCase,
    retrieved: list[dict[str, Any]],
    *,
    top_k: int,
) -> CaseResult:
    """对单条用例计算首个命中名次。"""
    for rank, item in enumerate(retrieved[:top_k], start=1):
        if matches_case(case, item):
            return CaseResult(query=case.query, hit_rank=rank, top_k=top_k)
    return CaseResult(query=case.query, hit_rank=None, top_k=top_k)


def build_report(
    results: list[CaseResult],
    *,
    top_k: int,
    notes: list[str] | None = None,
) -> EvalReport:
    return EvalReport(
        results=tuple(results),
        top_k=top_k,
        notes=tuple(notes or ()),
    )


def load_case(raw: dict[str, Any]) -> EvalCase:
    """从 JSONL 行构造用例（字段名与 golden_set.jsonl 对齐）。"""
    return EvalCase(
        query=str(raw.get("query") or ""),
        expect_doc_ids=tuple(str(x) for x in raw.get("expect_doc_ids") or ()),
        expect_any=tuple(str(x) for x in raw.get("expect_any") or ()),
    )


__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalReport",
    "build_report",
    "evaluate_case",
    "load_case",
    "matches_case",
]
