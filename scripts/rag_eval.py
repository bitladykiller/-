#!/usr/bin/env python
"""RAG 离线评测 CLI。

对 golden set 中的每个查询跑一次真实混合检索，输出 hit@k / MRR。

用法（需要可连通的 Milvus 且知识库已有文档）：

    python scripts/rag_eval.py                       # 默认 top_k=5
    python scripts/rag_eval.py --top-k 10
    python scripts/rag_eval.py --golden-set path.jsonl --json

调参对比工作流：
    1. 跑一次记下基线（建议把 --json 输出存档）
    2. 改检索参数（bm25_drop_ratio / rrf_k / rerank 开关 / 改写开关）
    3. 再跑一次对比 —— 让"感觉变好了"变成数字
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 允许直接 `python scripts/rag_eval.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge.application.rag_eval import (  # noqa: E402
    CaseResult,
    build_report,
    evaluate_case,
    load_case,
)

DEFAULT_GOLDEN_SET = Path(__file__).parent / "golden_set.jsonl"


def load_golden_set(path: Path):
    cases = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cases.append(load_case(json.loads(line)))
        except Exception as exc:
            raise SystemExit(f"golden set 第 {line_no} 行无效: {exc}") from exc
    if not cases:
        raise SystemExit("golden set 为空")
    return cases


async def run_eval(golden_path: Path, top_k: int, as_json: bool) -> int:
    from app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search import (
        get_shared_searcher,
    )

    cases = load_golden_set(golden_path)
    searcher = get_shared_searcher()

    results: list[CaseResult] = []
    for case in cases:
        retrieved = await searcher.search(case.query, top_k=top_k)
        outcome = evaluate_case(case, retrieved, top_k=top_k)
        results.append(outcome)
        if not as_json:
            mark = f"✓ rank={outcome.hit_rank}" if outcome.hit else "✗ miss"
            print(f"  {mark:<12} {case.query}")

    report = build_report(results, top_k=top_k)
    if as_json:
        print(
            json.dumps(
                {
                    "top_k": report.top_k,
                    "cases": report.case_count,
                    "hit_rate": round(report.hit_rate, 4),
                    "mrr": round(report.mrr, 4),
                    "misses": [r.query for r in report.results if not r.hit],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("\n=== RAG 评测报告 ===")
        print(report.summary_line())
        misses = [r.query for r in report.results if not r.hit]
        if misses:
            print(f"未命中 {len(misses)} 条:")
            for query in misses:
                print(f"  - {query}")
    # 有未命中不算失败退出：评测是度量，不是门禁；要做门禁请在 CI 里加阈值
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 离线评测")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于存档对比）")
    args = parser.parse_args()
    return asyncio.run(run_eval(args.golden_set, args.top_k, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
