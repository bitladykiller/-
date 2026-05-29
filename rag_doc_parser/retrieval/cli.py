"""
检索模块命令行工具。

用法:
  # 索引文档
  python -m rag_doc_parser.retrieval.cli index --file ./chunks.json

  # 搜索
  python -m rag_doc_parser.retrieval.cli search --query "查询文本"

  # 完整流程：解析 + 索引 + 搜索
  python -m rag_doc_parser.retrieval.cli full --pdf ./example.pdf --query "查询文本"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rag_doc_parser.config import ParserConfig
from rag_doc_parser.pipeline import parse_document
from rag_doc_parser.retrieval.config import RetrievalConfig
from rag_doc_parser.retrieval.hybrid_search import HybridSearcher


async def cmd_index(args):
    """索引命令：从 JSON 文件加载 chunks 并写入 Milvus + BM25。"""
    with open(args.file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 重建 DocumentChunk 对象
    from rag_doc_parser.models import DocumentChunk
    chunks = [DocumentChunk(**item) for item in data]

    config = RetrievalConfig()
    searcher = HybridSearcher(config)
    count = await searcher.index(chunks)
    print(f"索引完成: {count} 条记录")


async def cmd_search(args):
    """搜索命令：混合检索。"""
    config = RetrievalConfig()
    searcher = HybridSearcher(config)
    results = await searcher.search(args.query)

    for i, r in enumerate(results):
        print(f"\n--- 结果 {i+1} ---")
        print(f"类型: {r.get('chunk_type', '?')}")
        print(f"章节: {r.get('section_path', '?')}")
        print(f"文本: {r.get('raw_text', '')[:200]}...")
        print(f"来源: {r.get('source_file', '?')}")
        rrf = r.get("rrf_score", 0)
        rerank = r.get("rerank_score")
        if rerank is not None:
            print(f"分数: RRF={rrf:.4f} Rerank={rerank:.4f}")
        else:
            print(f"分数: RRF={rrf:.4f}")


async def cmd_full(args):
    """完整流程：解析 PDF/DOCX → 索引 → 搜索。"""
    print(f"Step 1/3: 解析文档 {args.pdf} ...")
    chunks = parse_document(args.pdf)
    print(f"  解析完成: {len(chunks)} 个 chunk")

    print("Step 2/3: 索引到 Milvus + BM25 ...")
    config = RetrievalConfig()
    searcher = HybridSearcher(config)
    count = await searcher.index(chunks)
    print(f"  索引完成: {count} 条")

    print(f"Step 3/3: 搜索 '{args.query}' ...")
    results = await searcher.search(args.query)
    for i, r in enumerate(results):
        print(f"\n--- 结果 {i+1} ---")
        print(f"类型: {r.get('chunk_type', '?')}")
        print(f"章节: {r.get('section_path', '?')}")
        print(f"文本: {r.get('raw_text', '')[:200]}...")


def main():
    parser = argparse.ArgumentParser(description="RAG 文档检索工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="索引 chunks JSON 文件")
    p.add_argument("--file", required=True, help="chunks.json 文件路径")

    p = sub.add_parser("search", help="混合检索")
    p.add_argument("--query", required=True, help="查询文本")

    p = sub.add_parser("full", help="完整流程（解析+索引+搜索）")
    p.add_argument("--pdf", required=True, help="PDF/DOCX 文件路径")
    p.add_argument("--query", required=True, help="查询文本")

    args = parser.parse_args()
    asyncio.run(
        {"index": cmd_index, "search": cmd_search, "full": cmd_full}[args.cmd](args)
    )


if __name__ == "__main__":
    main()
