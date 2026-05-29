"""
RAG 文档解析器 — Markdown 处理子包。

提供 Markdown 清洗、标题解析、内容块识别、表格工具等功能。
"""

from rag_doc_parser.markdown.cleaner import MarkdownCleaner
from rag_doc_parser.markdown.heading_parser import HeadingParser
from rag_doc_parser.markdown.block_parser import BlockParser
from rag_doc_parser.markdown.table_utils import (
    is_markdown_table,
    parse_markdown_table,
    build_markdown_table,
    count_table_rows,
)

__all__ = [
    "MarkdownCleaner",
    "HeadingParser",
    "BlockParser",
    "is_markdown_table",
    "parse_markdown_table",
    "build_markdown_table",
    "count_table_rows",
]
