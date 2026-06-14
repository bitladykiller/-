"""
RAG 文档解析器 — 基类。

定义 BaseDocumentParser 抽象基类，所有解析器必须实现 parse 方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from rag_doc_parser.config import ParserConfig
from rag_doc_parser.models import ParsedMarkdownDocument


class BaseDocumentParser(ABC):
    """文档解析器抽象基类。

    所有解析器（PDF、DOCX）都继承此类，实现 parse 方法。
    parse 方法接收文件路径和 doc_id，返回统一的 ParsedMarkdownDocument。

    Attributes:
        config: 解析器配置。
        parser_name: 解析器名称，用于日志和 metadata。
    """

    def __init__(self, config: Optional[ParserConfig] = None) -> None:
        """初始化解析器。

        Args:
            config: 解析器配置。如果为 None，使用默认配置。
        """
        self.config = config or ParserConfig()
        self.parser_name: str = self.__class__.__name__

    @abstractmethod
    def parse(self, file_path: str, doc_id: str) -> ParsedMarkdownDocument:
        """解析文档，返回统一的 Markdown 文档。

        Args:
            file_path: 文档文件路径。
            doc_id: 文档唯一标识。

        Returns:
            ParsedMarkdownDocument 实例。

        Raises:
            DocumentParseError: 解析失败时抛出。
        """
        ...

    def _validate_file(self, file_path: str, expected_extensions: list[str]) -> None:
        """校验文件是否存在且扩展名正确。

        Args:
            file_path: 文件路径。
            expected_extensions: 允许的扩展名列表，如 [".pdf"]。

        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: 扩展名不匹配。
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        if not path.is_file():
            raise ValueError(f"不是普通文件: {file_path}")

        ext = path.suffix.lower()
        if ext not in expected_extensions:
            raise ValueError(
                f"文件扩展名 {ext} 不在允许范围 {expected_extensions} 内"
            )

    def _build_metadata(self, **kwargs) -> dict:
        """构建通用 metadata 字典。

        自动注入 parser_name。

        Args:
            **kwargs: 其他元数据字段。

        Returns:
            metadata 字典。
        """
        meta = {"parser_name": self.parser_name}
        meta.update(kwargs)
        return meta
