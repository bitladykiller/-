"""
RAG 文档解析与切分 — 主控管线。

将原始文件（PDF/DOCX）经解析→清洗→切分→分块，输出 DocumentChunk 列表。
"""

from __future__ import annotations

import logging
import uuid

from app.knowledge.infrastructure.doc_parser.config import ParserConfig
from app.knowledge.infrastructure.doc_parser.exceptions import (
    DocumentParseError,
    UnsupportedFileTypeError,
)
from app.knowledge.infrastructure.doc_parser.markdown.block_parser import BlockParser
from app.knowledge.infrastructure.doc_parser.markdown.cleaner import MarkdownCleaner
from app.knowledge.infrastructure.doc_parser.markdown.heading_parser import HeadingParser
from app.knowledge.infrastructure.doc_parser.models import DocumentChunk
from app.knowledge.infrastructure.doc_parser.parsers.docling_docx_parser import DoclingDOCXParser
from app.knowledge.infrastructure.doc_parser.parsers.docling_pdf_parser import DoclingPDFParser
from app.knowledge.infrastructure.doc_parser.parsers.docx_fallback_parser import DocxFallbackParser
from app.knowledge.infrastructure.doc_parser.splitters.code_splitter import CodeSplitter
from app.knowledge.infrastructure.doc_parser.splitters.table_splitter import TableSplitter
from app.knowledge.infrastructure.doc_parser.splitters.text_splitter import TextSplitter

logger = logging.getLogger(__name__)


def parse_document(
    file_path: str,
    doc_id: str | None = None,
    config: ParserConfig | None = None,
) -> list[DocumentChunk]:
    """解析单个文档，返回 DocumentChunk 列表。

    Args:
        file_path: PDF/DOCX 文件路径。
        doc_id: 文档 ID，默认自动生成。
        config: 解析配置，默认 from_env()。

    Returns:
        DocumentChunk 列表，可直接写入向量数据库。

    Raises:
        UnsupportedFileTypeError: 不支持的文件类型。
        DocumentParseError: 解析过程出错。
    """
    if config is None:
        config = ParserConfig.from_env()
    if doc_id is None:
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"

    # 1. 解析文件 → Markdown
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if ext == "pdf":
        parser = DoclingPDFParser(config)
    elif ext == "docx":
        parser = DoclingDOCXParser(config)  # type: ignore[assignment]
    else:
        raise UnsupportedFileTypeError(file_path)

    try:
        doc = parser.parse(file_path, doc_id)
    except DocumentParseError:
        # DOCX fallback: Docling 失败后用 python-docx
        if file_path.lower().endswith(".docx"):
            logger.warning("Docling DOCX 解析失败，尝试 python-docx fallback")
            fallback = DocxFallbackParser(config)
            doc = fallback.parse(file_path, doc_id)
        else:
            raise

    markdown = doc.markdown
    source_file = file_path
    parser_name = doc.metadata.get("parser_name", "unknown")

    # 2. 清洗 Markdown
    if config.enable_markdown_cleaning:
        cleaner = MarkdownCleaner()
        markdown = cleaner.clean(markdown)

    # 3. 标题解析 → Sections
    heading_parser = HeadingParser()
    sections = heading_parser.parse(markdown)

    # 4. Block 识别 + 切分
    block_parser = BlockParser()
    text_splitter = TextSplitter(config.text_chunk_size, config.text_chunk_overlap)
    chunks: list[DocumentChunk] = []

    for section in sections:
        blocks = block_parser.parse(section)

        for block in blocks:
            if block.block_type == "table" and config.enable_table_split:
                table_splitter = TableSplitter(config.max_table_rows_per_chunk)
                table_chunks = table_splitter.split(
                    block,
                    doc_id,
                    source_file,
                    chunk_id_prefix=f"{doc_id}_{block.block_id}_",
                )
                chunks.extend([chunk for chunk in table_chunks if chunk.raw_text.strip()])
                continue

            if block.block_type == "code" and config.enable_code_split:
                code_splitter = CodeSplitter(config.max_code_lines_per_chunk)
                pieces = code_splitter.split(
                    block.content,
                    block.metadata.get("language", "") or "",
                )
            elif block.block_type == "image_caption":
                # 图片说明通常完整保留，太长才切。
                if len(block.content) > config.text_chunk_size:
                    pieces = text_splitter.split(block.content)
                else:
                    pieces = [block.content]
            else:
                pieces = text_splitter.split(block.content)

            for i, piece in enumerate(pieces):
                chunk_id = f"{doc_id}_{block.block_id}_{i}"
                raw_text = piece.strip()
                if not raw_text:
                    continue

                # embedding_text 必须带标题路径
                if block.section_path:
                    embedding_text = f"{block.section_path}\n\n{raw_text}"
                else:
                    embedding_text = raw_text

                chunk = DocumentChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    source_file=source_file,
                    chunk_type=block.block_type,
                    section_path=block.section_path,
                    h1=block.h1,
                    h2=block.h2,
                    h3=block.h3,
                    h4=block.h4,
                    raw_text=raw_text,
                    embedding_text=embedding_text,
                    page_start=block.page_start,
                    page_end=block.page_end,
                    table_id=block.metadata.get("table_id"),
                    row_start=block.metadata.get("row_start"),
                    row_end=block.metadata.get("row_end"),
                    language=block.metadata.get("language"),
                    metadata={
                        "doc_id": doc_id,
                        "source_file": source_file,
                        "section_path": block.section_path,
                        "chunk_type": block.block_type,
                        "parser_name": parser_name,
                        "block_id": block.block_id,
                    },
                )
                chunks.append(chunk)

    logger.info(f"文档 {doc_id} 完成解析，共 {len(chunks)} 个 chunk")
    return chunks
