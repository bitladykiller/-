"""RAG 评测指标层单测（纯函数，不连 Milvus）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.knowledge.application.rag_eval import (
    EvalCase,
    build_report,
    evaluate_case,
    load_case,
    matches_case,
)


def _case(**kwargs) -> EvalCase:
    defaults = {"query": "门铃怎么连 WiFi", "expect_any": ("门铃",)}
    defaults.update(kwargs)
    return EvalCase(**defaults)


def test_case_requires_expectation() -> None:
    with pytest.raises(ValueError, match="expect"):
        EvalCase(query="q")
    with pytest.raises(ValueError, match="query"):
        EvalCase(query="  ", expect_any=("x",))


def test_matches_by_doc_id_or_substring() -> None:
    by_doc = _case(expect_doc_ids=("doc_a",), expect_any=())
    by_text = _case(expect_any=("wifi",))

    assert matches_case(by_doc, {"doc_id": "doc_a", "raw_text": ""}) is True
    assert matches_case(by_doc, {"doc_id": "doc_b", "raw_text": ""}) is False
    # 子串匹配大小写不敏感
    assert matches_case(by_text, {"doc_id": "", "raw_text": "连接 WiFi 的步骤"}) is True
    assert matches_case(by_text, {"doc_id": "", "raw_text": "蓝牙配对"}) is False


def test_evaluate_case_records_first_hit_rank() -> None:
    case = _case(expect_any=("门铃",))
    retrieved = [
        {"doc_id": "x", "raw_text": "无关"},
        {"doc_id": "y", "raw_text": "智能门铃安装指南"},
        {"doc_id": "z", "raw_text": "门铃说明书"},
    ]

    outcome = evaluate_case(case, retrieved, top_k=5)

    assert outcome.hit_rank == 2
    assert outcome.reciprocal_rank == 0.5


def test_evaluate_case_respects_top_k_cutoff() -> None:
    case = _case(expect_any=("门铃",))
    retrieved = [{"raw_text": "无关"}, {"raw_text": "门铃"}]

    outcome = evaluate_case(case, retrieved, top_k=1)

    assert outcome.hit is False
    assert outcome.reciprocal_rank == 0.0


def test_report_metrics() -> None:
    results = [
        evaluate_case(_case(expect_any=("A",)), [{"raw_text": "A"}], top_k=5),   # rank 1
        evaluate_case(_case(expect_any=("B",)), [{"raw_text": "x"}, {"raw_text": "B"}], top_k=5),  # rank 2
        evaluate_case(_case(expect_any=("C",)), [{"raw_text": "x"}], top_k=5),   # miss
    ]

    report = build_report(results, top_k=5)

    assert report.case_count == 3
    assert report.hit_rate == pytest.approx(2 / 3)
    assert report.mrr == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert "hit@5=0.667" in report.summary_line()


def test_empty_report_is_zero_not_crash() -> None:
    report = build_report([], top_k=5)

    assert report.hit_rate == 0.0
    assert report.mrr == 0.0


def test_golden_set_file_is_well_formed() -> None:
    """评测集本身必须可加载——坏行在 CI 就暴露，而不是评测时。"""
    path = Path(__file__).resolve().parents[2] / "scripts" / "golden_set.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    cases = [load_case(json.loads(line)) for line in lines]

    assert len(cases) >= 20
    assert all(case.expect_any or case.expect_doc_ids for case in cases)
