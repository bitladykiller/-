"""
RAG 文档解析与切分模块 — 异常类。

所有异常信息必须清晰，方便定位问题。
"""


class UnsupportedFileTypeError(Exception):
    """不支持的文件类型。

    例如用户传入 .png / .mp4 等非 Markdown/PDF/DOCX 文件。
    """

    def __init__(self, file_path: str):
        ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "unknown"
        super().__init__(
            f"不支持的文件类型: .{ext}（文件: {file_path}）。"
            f"目前只支持 .md / .markdown / .pdf / .docx。"
        )


class DocumentParseError(Exception):
    """文档解析通用异常。

    所有解析器异常的基类。
    """

    def __init__(self, message: str, file_path: str = "", parser_name: str = ""):
        detail = f"[{parser_name}] " if parser_name else ""
        detail += f"解析失败: {message}"
        if file_path:
            detail += f"（文件: {file_path}）"
        super().__init__(detail)


class DoclingParseError(DocumentParseError):
    """Docling 解析器异常。

    用于 Docling 对 PDF 或 DOCX 解析出错时抛出。
    """

    def __init__(self, message: str, file_path: str = ""):
        super().__init__(message, file_path=file_path, parser_name="Docling")

