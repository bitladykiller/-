"""
RAG 文档解析与切分模块。

将 PDF / DOCX 文档统一解析为 Markdown，再切分为可入库的 DocumentChunk。
"""

from rag_doc_parser.config import ParserConfig
from rag_doc_parser.models import (
    DocumentChunk,
    MarkdownBlock,
    MarkdownSection,
    PageMarkdown,
    ParsedMarkdownDocument,
    new_doc_id,
    new_uuid,
)
from rag_doc_parser.exceptions import (
    ChunkBuildError,
    DoclingParseError,
    DocumentParseError,
    MarkdownParseError,
    UnsupportedFileTypeError,
)
from rag_doc_parser.pipeline import parse_document

__all__ = [
    "ParserConfig",
    "DocumentChunk",
    "MarkdownBlock",
    "MarkdownSection",
    "PageMarkdown",
    "ParsedMarkdownDocument",
    "new_doc_id",
    "new_uuid",
    "ChunkBuildError",
    "DoclingParseError",
    "DocumentParseError",
    "MarkdownParseError",
    "UnsupportedFileTypeError",
    "parse_document",
]
