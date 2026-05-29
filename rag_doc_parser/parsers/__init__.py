"""
RAG 文档解析器子包。

提供多种文档解析器，统一输出 ParsedMarkdownDocument。
"""

from rag_doc_parser.parsers.base import BaseDocumentParser
from rag_doc_parser.parsers.docling_pdf_parser import DoclingPDFParser
from rag_doc_parser.parsers.docling_docx_parser import DoclingDOCXParser
from rag_doc_parser.parsers.docx_fallback_parser import DocxFallbackParser

__all__ = [
    "BaseDocumentParser",
    "DoclingPDFParser",
    "DoclingDOCXParser",
    "DocxFallbackParser",
]
