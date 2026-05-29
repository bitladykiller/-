"""
RAG 文档解析器 — 文本切分器子包。

提供文本、表格、代码块的切分功能。
"""

from rag_doc_parser.splitters.text_splitter import TextSplitter
from rag_doc_parser.splitters.table_splitter import TableSplitter
from rag_doc_parser.splitters.code_splitter import CodeSplitter

__all__ = [
    "TextSplitter",
    "TableSplitter",
    "CodeSplitter",
]
