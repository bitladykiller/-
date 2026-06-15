"""
RAG 文档解析器 — 表格切分器。

当表格行数超过阈值时，按指定行数切分，每个子表格重复表头。
"""

import logging

from rag_doc_parser.markdown.table_utils import (
    build_markdown_table,
    parse_markdown_table,
)
from rag_doc_parser.models import DocumentChunk, MarkdownBlock, new_uuid

logger = logging.getLogger(__name__)


class TableSplitter:
    """表格切分器。

    当表格数据行数超过 max_rows_per_chunk 时，按指定行数切分为多个子表格。
    每个子表格都会重复表头行。

    Attributes:
        max_rows_per_chunk: 每个子表格的最大数据行数。
    """

    def __init__(self, max_rows_per_chunk: int = 50) -> None:
        """初始化表格切分器。

        Args:
            max_rows_per_chunk: 每个子表格的最大数据行数，默认 50。
        """
        self.max_rows_per_chunk = max_rows_per_chunk

    def split(
        self,
        block: MarkdownBlock,
        doc_id: str,
        source_file: str,
        chunk_id_prefix: str = "",
    ) -> list[DocumentChunk]:
        """切分表格块为 DocumentChunk 列表。

        如果表格行数不超过阈值，返回单个 chunk。
        否则按 max_rows_per_chunk 切分，每个子表格重复表头。

        Args:
            block: MarkdownBlock 实例（block_type="table"）。
            doc_id: 文档 ID。
            source_file: 原始文件路径。
            chunk_id_prefix: chunk_id 前缀（可选）。

        Returns:
            DocumentChunk 列表。
        """
        headers, rows = parse_markdown_table(block.content)

        # 解析失败，返回原始内容
        if not headers:
            return [self._make_chunk(
                block=block,
                doc_id=doc_id,
                source_file=source_file,
                raw_text=block.content,
                chunk_id_prefix=chunk_id_prefix,
            )]

        # 行数不超过阈值，不切分
        if len(rows) <= self.max_rows_per_chunk:
            return [self._make_chunk(
                block=block,
                doc_id=doc_id,
                source_file=source_file,
                raw_text=block.content,
                chunk_id_prefix=chunk_id_prefix,
            )]

        # 按行数切分
        chunks: list[DocumentChunk] = []

        for start_idx in range(0, len(rows), self.max_rows_per_chunk):
            end_idx = min(start_idx + self.max_rows_per_chunk, len(rows))
            sub_rows = rows[start_idx:end_idx]

            # 构建子表格 Markdown（重复表头）
            sub_table_text = build_markdown_table(headers, sub_rows)

            chunk = self._make_chunk(
                block=block,
                doc_id=doc_id,
                source_file=source_file,
                raw_text=sub_table_text,
                chunk_id_prefix=chunk_id_prefix,
            )
            chunks.append(chunk)

        logger.debug(
            "表格切分: %d 行 -> %d 个子表格 (每表最多 %d 行)",
            len(rows), len(chunks), self.max_rows_per_chunk,
        )

        return chunks

    @staticmethod
    def _make_chunk(
        block: MarkdownBlock,
        doc_id: str,
        source_file: str,
        raw_text: str,
        chunk_id_prefix: str = "",
    ) -> DocumentChunk:
        """构建表格 DocumentChunk。

        Args:
            block: 原始 MarkdownBlock。
            doc_id: 文档 ID。
            source_file: 原始文件路径。
            raw_text: 表格文本。
            chunk_id_prefix: chunk_id 前缀。

        Returns:
            DocumentChunk 实例。
        """
        # embedding_text: section_path 前缀 + 表格内容
        prefix = f"[{block.section_path}] " if block.section_path else ""
        embedding_text = prefix + raw_text

        chunk_id = f"{chunk_id_prefix}tc_{new_uuid()}" if chunk_id_prefix else f"tc_{new_uuid()}"

        return DocumentChunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            source_file=source_file,
            chunk_type="table",
            section_path=block.section_path,
            raw_text=raw_text,
            embedding_text=embedding_text,
        )
