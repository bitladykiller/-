"""
RAG 文档解析器 — Markdown 直读解析器。

PDF / DOCX 会先转成 Markdown 再切分；原生 .md 文件无需 Docling，
直接按 UTF-8 读入后进入同一套清洗与分块流程。
"""

from __future__ import annotations

import logging

from app.knowledge.infrastructure.doc_parser.config import ParserConfig
from app.knowledge.infrastructure.doc_parser.exceptions import DocumentParseError
from app.knowledge.infrastructure.doc_parser.models import PageMarkdown, ParsedMarkdownDocument
from app.knowledge.infrastructure.doc_parser.parsers.base import BaseDocumentParser

logger = logging.getLogger(__name__)

# 常见 Markdown 扩展名
_MARKDOWN_EXTENSIONS = [".md", ".markdown"]


class MarkdownFileParser(BaseDocumentParser):
    """原生 Markdown 文件解析器。

    职责：
    1. 校验扩展名为 .md / .markdown
    2. 以 UTF-8（失败则回退常见编码）读取全文
    3. 包装为 ParsedMarkdownDocument，供 pipeline 统一切分
    """

    def __init__(self, config: ParserConfig | None = None) -> None:
        super().__init__(config)
        self.parser_name = "MarkdownFileParser"

    def parse(self, file_path: str, doc_id: str) -> ParsedMarkdownDocument:
        """读取 Markdown 文件并返回统一文档模型。

        Args:
            file_path: Markdown 文件路径。
            doc_id: 文档唯一标识。

        Returns:
            ParsedMarkdownDocument 实例。

        Raises:
            DocumentParseError: 读取或解码失败。
        """
        self._validate_file(file_path, _MARKDOWN_EXTENSIONS)
        logger.info(
            "[%s] 开始读取 Markdown: %s (doc_id=%s)",
            self.parser_name,
            file_path,
            doc_id,
        )

        try:
            markdown_text = self._read_text(file_path)
        except OSError as exc:
            raise DocumentParseError(
                f"读取文件失败: {exc}",
                file_path=file_path,
                parser_name=self.parser_name,
            ) from exc
        except UnicodeError as exc:
            raise DocumentParseError(
                f"文本解码失败: {exc}",
                file_path=file_path,
                parser_name=self.parser_name,
            ) from exc

        page_markdown_list = [
            PageMarkdown(
                page_number=1,
                markdown=markdown_text,
                metadata={"source": "markdown_file"},
            )
        ]
        metadata = self._build_metadata(
            picture_count=0,
            table_count=markdown_text.count("|---"),
            page_count=1,
            source="markdown_file",
        )
        return ParsedMarkdownDocument(
            doc_id=doc_id,
            source_file=file_path,
            markdown=markdown_text,
            page_markdown_list=page_markdown_list,
            metadata=metadata,
        )

    def _read_text(self, file_path: str) -> str:
        """按 UTF-8 优先读取文本；失败时尝试常见中文编码。

        WHY: 部分 Windows 导出的 md 可能是 GBK/GB18030，直接 UTF-8 会失败。
        """
        from pathlib import Path

        raw = Path(file_path).read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        # latin-1 理论上总能解码任意字节；上面循环应已返回
        return raw.decode("utf-8", errors="replace")
