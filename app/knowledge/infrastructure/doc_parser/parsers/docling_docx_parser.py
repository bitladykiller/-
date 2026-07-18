"""
RAG 文档解析器 — Docling DOCX 解析器。

使用 Docling 的 DocumentConverter 解析 DOCX 文档。
"""

from __future__ import annotations

import logging
import warnings
from typing import Optional

from app.knowledge.infrastructure.doc_parser.config import ParserConfig
from app.knowledge.infrastructure.doc_parser.exceptions import DoclingParseError
from app.knowledge.infrastructure.doc_parser.models import PageMarkdown, ParsedMarkdownDocument
from app.knowledge.infrastructure.doc_parser.parsers.base import BaseDocumentParser

logger = logging.getLogger(__name__)


class DoclingDOCXParser(BaseDocumentParser):
    """基于 Docling 的 DOCX 解析器。

    使用 DocumentConverter 将 DOCX 转为 Markdown。
    如果 Docling 不可用或解析失败，调用方应降级到 DocxFallbackParser。
    """

    def __init__(self, config: Optional[ParserConfig] = None) -> None:
        """初始化 Docling DOCX 解析器。"""
        super().__init__(config)
        self.parser_name = "DoclingDOCXParser"

    def parse(self, file_path: str, doc_id: str) -> ParsedMarkdownDocument:
        """解析 DOCX 文档为统一 Markdown 格式。

        Args:
            file_path: DOCX 文件路径。
            doc_id: 文档唯一标识。

        Returns:
            ParsedMarkdownDocument 实例。

        Raises:
            DoclingParseError: 解析失败时抛出。
        """
        self._validate_file(file_path, [".docx"])
        logger.info("[%s] 开始解析 DOCX: %s (doc_id=%s)", self.parser_name, file_path, doc_id)

        try:
            from docling.document_converter import DocumentConverter

            # 构建转换器（DOCX 通常不需要特殊管道选项）
            converter = DocumentConverter()

            # 执行转换
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = converter.convert(file_path)

            # 提取文档对象
            doc = result.document

            # 导出 Markdown
            markdown_text = doc.export_to_markdown()

            # 统计信息
            page_count = self._count_pages(doc)
            table_count = self._count_tables(doc)

            # 构建 metadata
            metadata = self._build_metadata(
                picture_count=0,
                table_count=table_count,
                page_count=page_count,
                warnings=[],
            )

            # 构建 page_markdown_list
            page_markdown_list = [
                PageMarkdown(
                    page_number=1,
                    markdown=markdown_text,
                    metadata={"source": "docling_docx"},
                )
            ]

            logger.info(
                "[%s] DOCX 解析完成: pages=%d, tables=%d",
                self.parser_name, page_count, table_count,
            )

            return ParsedMarkdownDocument(
                doc_id=doc_id,
                source_file=file_path,
                markdown=markdown_text,
                page_markdown_list=page_markdown_list,
                metadata=metadata,
            )

        except Exception as e:
            raise DoclingParseError(
                f"DOCX 解析失败: {e}", file_path=file_path
            ) from e

    def _count_pages(self, doc) -> int:
        """统计文档页数。"""
        pages = getattr(doc, "pages", None)
        if pages is not None:
            try:
                return len(pages)
            except TypeError:
                pass
        return 0

    def _count_tables(self, doc) -> int:
        """统计文档中表格数量。"""
        count = 0
        try:
            for item, _ in doc.iterate_items():
                item_type = getattr(item, "type", None) or getattr(item, "label", None)
                type_str = str(item_type).lower() if item_type else ""
                if "table" in type_str:
                    count += 1
        except (AttributeError, TypeError):
            pass
        return count
