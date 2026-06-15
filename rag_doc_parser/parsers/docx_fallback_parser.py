"""
RAG 文档解析器 — python-docx 兜底解析器。

当 Docling 不可用或解析失败时，使用 python-docx 解析 DOCX。
实现 iter_block_items 以保留段落和表格的原始顺序。
"""

import logging
from typing import Any

from rag_doc_parser.config import ParserConfig
from rag_doc_parser.exceptions import DocumentParseError
from rag_doc_parser.parsers.base import BaseDocumentParser

logger = logging.getLogger(__name__)


class DocxFallbackParser(BaseDocumentParser):
    """基于 python-docx 的 DOCX 兜底解析器。

    当 Docling 不可用时使用。支持：
    - 段落（标题、正文、列表）
    - 表格（转为 Markdown 表格）
    - 保留段落和表格的原始顺序
    """

    def __init__(self, config: ParserConfig | None = None) -> None:
        """初始化 python-docx 兜底解析器。"""
        super().__init__(config)
        self.parser_name = "DocxFallbackParser"

    def parse(self, file_path: str) -> str:
        """解析 DOCX 文档为统一 Markdown 格式。

        Args:
            file_path: DOCX 文件路径。

        Returns:
            统一的 Markdown 文本。

        Raises:
            DocumentParseError: 解析失败时抛出。
        """
        self._validate_file(file_path, [".docx"])
        logger.info("[%s] 开始解析 DOCX: %s", self.parser_name, file_path)

        try:
            from docx import Document

            doc = Document(file_path)

            # 按顺序遍历段落和表格，生成 Markdown
            markdown_lines: list[str] = []
            table_count = 0

            for block in self._iter_block_items(doc):
                block_type, content = block

                if block_type == "paragraph":
                    md = self._paragraph_to_markdown(content)
                    if md:
                        markdown_lines.append(md)

                elif block_type == "table":
                    md = self._table_to_markdown(content)
                    if md:
                        markdown_lines.append(md)
                        table_count += 1

            markdown_text = "\n\n".join(markdown_lines)

            logger.info("[%s] DOCX 解析完成: tables=%d", self.parser_name, table_count)

            return markdown_text

        except Exception as e:
            raise DocumentParseError(
                str(e), file_path=file_path, parser_name=self.parser_name
            ) from e

    @staticmethod
    def _iter_block_items(doc) -> Any:
        """按文档顺序遍历段落和表格。"""
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        body = doc.element.body
        for child in body:
            tag = child.tag
            if tag == qn("w:p"):
                yield ("paragraph", Paragraph(child, doc))
            elif tag == qn("w:tbl"):
                yield ("table", Table(child, doc))

    def _paragraph_to_markdown(self, paragraph) -> str:
        """将段落转为 Markdown。"""
        text = paragraph.text.strip()
        if not text:
            return ""

        style_name = (paragraph.style.name or "").lower()
        if "heading" in style_name:
            try:
                level = int(style_name.replace("heading", "").strip())
                level = min(max(level, 1), 6)
            except ValueError:
                level = 1
            return f"{'#' * level} {text}"

        if "list" in style_name:
            if "number" in style_name:
                return f"1. {text}"
            return f"- {text}"

        return text

    def _table_to_markdown(self, table) -> str:
        """将表格转为 Markdown 表格格式。"""
        rows_data: list[list[str]] = []
        for row in table.rows:
            cells = [cell.text.strip().replace("|", "\\|") for cell in row.cells]
            rows_data.append(cells)

        if not rows_data:
            return ""

        max_cols = max(len(row) for row in rows_data)
        for row in rows_data:
            while len(row) < max_cols:
                row.append("")

        header = rows_data[0]
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * max_cols) + " |",
        ]
        for row in rows_data[1:]:
            lines.append("| " + " | ".join(row) + " |")

        return "\n".join(lines)
