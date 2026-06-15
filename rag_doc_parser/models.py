"""
RAG 文档解析与切分模块 — 核心数据模型。

定义从原始文档到最终入库的所有数据结构。
"""

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
class MarkdownSection:
    """按多级标题划分的章节。

    Attributes:
        section_path: 完整标题路径，如 "数据库 > 事务管理 > 隔离级别"。
        content: 该标题下的正文内容（不含标题行本身）。
    """

    section_path: str = ""
    content: str = ""


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
    """

    chunk_id: str = ""
    doc_id: str = ""
    source_file: str = ""
    chunk_type: str = "text"
    section_path: str = ""
    raw_text: str = ""
    embedding_text: str = ""


def new_uuid() -> str:
    return uuid.uuid4().hex[:12]
