"""上传与索引共享的文档格式契约。

这个模块是“文档上传 -> 后台索引”链路里唯一的格式真相来源。
上传接口和索引服务都应该引用这里，避免两边各维护一份扩展名列表，
结果出现“接口允许上传，但索引器其实不支持”的边界漂移。

当前能力边界以 `rag_doc_parser` 为准：只支持 `.pdf` 和 `.docx`。
"""
from __future__ import annotations

from pathlib import Path

INDEXABLE_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx"})

# 只保留当前真实支持的类型，避免未接入解析链路的扩展名继续误导维护者。
DOCUMENT_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),  # OOXML 格式
}


def get_document_extension(filename: str | Path | None) -> str:
    """提取文件扩展名并统一转成小写。"""
    return Path(filename or "").suffix.lower()


def supports_document_indexing(extension: str) -> bool:
    """判断扩展名是否在当前上传+索引链路支持范围内。"""
    return extension in INDEXABLE_DOCUMENT_EXTENSIONS
