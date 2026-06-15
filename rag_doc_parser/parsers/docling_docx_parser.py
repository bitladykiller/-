"""
RAG 文档解析器 — Docling DOCX 解析器。

使用 Docling 的 DocumentConverter 解析 DOCX 文档。
"""

import logging
import warnings

from rag_doc_parser.config import ParserConfig
from rag_doc_parser.exceptions import DoclingParseError
from rag_doc_parser.parsers.base import BaseDocumentParser

logger = logging.getLogger(__name__)


class DoclingDOCXParser(BaseDocumentParser):
    """基于 Docling 的 DOCX 解析器。

    使用 DocumentConverter 将 DOCX 转为 Markdown。
    如果 Docling 不可用或解析失败，调用方应降级到 DocxFallbackParser。
    """

    def __init__(self, config: ParserConfig | None = None) -> None:
        """初始化 Docling DOCX 解析器。"""
        super().__init__(config)
        self.parser_name = "DoclingDOCXParser"

    def parse(self, file_path: str) -> str:
        """解析 DOCX 文档为统一 Markdown 格式。

        Args:
            file_path: DOCX 文件路径。

        Returns:
            统一的 Markdown 文本。

        Raises:
            DoclingParseError: 解析失败时抛出。
        """
        self._validate_file(file_path, [".docx"])
        logger.info("[%s] 开始解析 DOCX: %s", self.parser_name, file_path)

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
            pages = getattr(doc, "pages", None)
            page_count = 0
            if pages is not None:
                try:
                    page_count = len(pages)
                except TypeError:
                    page_count = 0

            table_count = 0
            try:
                for item, _ in doc.iterate_items():
                    item_type = getattr(item, "type", None) or getattr(item, "label", None)
                    type_str = str(item_type).lower() if item_type else ""
                    if "table" in type_str:
                        table_count += 1
            except (AttributeError, TypeError):
                table_count = 0

            logger.info(
                "[%s] DOCX 解析完成: pages=%d, tables=%d",
                self.parser_name, page_count, table_count,
            )

            return markdown_text

        except Exception as e:
            raise DoclingParseError(
                f"DOCX 解析失败: {e}", file_path=file_path
            ) from e
