"""
RAG 文档解析与切分模块 — 命令行工具。

用法：
  python -m rag_doc_parser.cli --file ./example.pdf --output ./chunks.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from rag_doc_parser.config import ParserConfig
from rag_doc_parser.pipeline import parse_document


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="RAG 文档解析与切分工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m rag_doc_parser.cli --file ./example.pdf --output ./chunks.json
  python -m rag_doc_parser.cli --file ./report.docx --output ./chunks.json --no-picture-description
        """,
    )

    parser.add_argument("--file", required=True, help="输入文件路径（PDF 或 DOCX）")
    parser.add_argument("--output", required=True, help="输出 JSON 文件路径")
    parser.add_argument("--doc-id", default=None, help="文档 ID（默认自动生成）")
    parser.add_argument("--chunk-size", type=int, default=700, help="文本 chunk 大小")
    parser.add_argument("--chunk-overlap", type=int, default=100, help="文本 chunk 重叠量")
    parser.add_argument(
        "--no-picture-description",
        action="store_true",
        help="关闭图片描述（使用本地 Docling 能力）",
    )
    parser.add_argument("--vlm-api-base-url", default=None, help="VLM API 地址")
    parser.add_argument("--vlm-model", default=None, help="VLM 模型名称")

    args = parser.parse_args()

    # 构造配置
    config = ParserConfig.from_env()
    config.text_chunk_size = args.chunk_size
    config.text_chunk_overlap = args.chunk_overlap

    if args.no_picture_description:
        config.docling_do_picture_description = False
        config.docling_enable_remote_services = False
    if args.vlm_api_base_url:
        config.vlm_api_base_url = args.vlm_api_base_url
    if args.vlm_model:
        config.vlm_model = args.vlm_model

    # 校验输入文件
    if not os.path.exists(args.file):
        print(f"错误: 文件不存在 — {args.file}", file=sys.stderr)
        sys.exit(1)

    print(f"正在解析: {args.file} ...")
    chunks = parse_document(args.file, doc_id=args.doc_id, config=config)

    # 输出 JSON
    output_data = [c.to_dict() for c in chunks]
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"完成: {len(chunks)} 个 chunk → {args.output}")
