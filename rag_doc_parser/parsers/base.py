"""
RAG 文档解析器 — 基类。

定义 BaseDocumentParser 抽象基类，所有解析器必须实现 parse 方法。
"""

from abc import ABC, abstractmethod
from pathlib import Path

from rag_doc_parser.config import ParserConfig


class BaseDocumentParser(ABC):
    """文档解析器抽象基类。

    所有解析器（PDF、DOCX）都继承此类，实现 parse 方法。
    parse 方法接收文件路径，返回统一的 Markdown 文本。

    Attributes:
        config: 解析器配置。
        parser_name: 解析器名称，用于日志。
    """

    def __init__(self, config: ParserConfig | None = None) -> None:
        """初始化解析器。

        Args:
            config: 解析器配置。如果为 None，使用默认配置。
        """
        self.config = config or ParserConfig()
        self.parser_name: str = self.__class__.__name__

    @abstractmethod
    def parse(self, file_path: str) -> str:
        """解析文档，返回统一的 Markdown 文档。

        Args:
            file_path: 文档文件路径。

        Returns:
            统一的 Markdown 文本。

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
