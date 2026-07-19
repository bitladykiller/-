"""
RAG 文档解析与切分模块 — 核心数据模型。

定义从原始文档到最终入库的所有数据结构。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

# RAG = Retrieval-Augmented Generation，检索增强生成
# PDF = Portable Document Format，便携式文档格式
# DOCX = Office Open XML Document，Word 文档格式
# MD = Markdown，轻量级标记语言
# VLM = Vision-Language Model，视觉语言模型
# OCR = Optical Character Recognition，光学字符识别
# UUID = Universally Unique Identifier，通用唯一标识符


@dataclass
class PageMarkdown:
    """单个页面的 Markdown 表示。

    用于保留页级信息，方便后续定位原文页码。
    """

    page_number: int | None = None
    markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedMarkdownDocument:
    """完成解析的统一 Markdown 文档。

    PDF 和 DOCX 都最终统一为此格式，然后进入同一套切分流程。

    Attributes:
        doc_id: 文档唯一标识。
        source_file: 原始文件路径。
        markdown: 最终统一 Markdown 全文。
        page_markdown_list: 按页拆分的 Markdown（保留页级信息）。
        metadata: Docling 原始统计、解析器名称、错误警告等。
    """

    doc_id: str
    source_file: str
    markdown: str = ""
    page_markdown_list: list[PageMarkdown] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarkdownSection:
    """按多级标题划分的章节。

    Attributes:
        section_id: 章节唯一标识。
        level: 标题级别（1-4）。
        title: 标题文本（不含 # 前缀）。
        section_path: 完整标题路径，如 "数据库 > 事务管理 > 隔离级别"。
        h1/h2/h3/h4: 当前各级标题。
        content: 该标题下的正文内容（不含标题行本身）。
        page_start/page_end: 起止页码。
    """

    section_id: str = ""
    level: int = 0
    title: str = ""
    section_path: str = ""
    h1: str | None = None
    h2: str | None = None
    h3: str | None = None
    h4: str | None = None
    content: str = ""
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarkdownBlock:
    """在章节内部按内容类型（文本/表格/代码/图片说明）细分的块。

    BlockType:
        - text: 普通文本段落
        - table: Markdown 表格
        - code: 代码块（``` 包裹）
        - image_caption: 图片说明
    """

    block_id: str = ""
    block_type: Literal["text", "table", "code", "image_caption"] = "text"
    content: str = ""
    section_path: str = ""
    h1: str | None = None
    h2: str | None = None
    h3: str | None = None
    h4: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentChunk:
    """最终的切分块，可直接写入向量数据库。

    Attributes:
        chunk_id: 唯一标识。
        doc_id: 所属文档 ID。
        source_file: 原始文件路径。
        chunk_type: 块类型（text/table/code/image_caption）。
        section_path: 标题路径。
        raw_text: 干净原文（不加标题路径），用于展示。
        embedding_text: 带标题路径的文本，用于向量化。
        page_start/page_end: 起止页码。
        table_id/row_start/row_end: 表格增补字段。
        language: 代码块语言。
        metadata: 额外元信息（doc_id, source_file, section_path, chunk_type, parser_name, block_id）。
    """

    chunk_id: str = ""
    doc_id: str = ""
    source_file: str = ""
    chunk_type: str = "text"
    section_path: str = ""
    h1: str | None = None
    h2: str | None = None
    h3: str | None = None
    h4: str | None = None
    raw_text: str = ""
    embedding_text: str = ""
    page_start: int | None = None
    page_end: int | None = None
    table_id: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的字典。"""
        from dataclasses import asdict
        return asdict(self)


def new_uuid() -> str:
    return uuid.uuid4().hex[:12]
