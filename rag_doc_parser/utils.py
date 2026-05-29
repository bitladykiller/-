"""
RAG 文档解析与切分模块 — 通用工具函数。

提供文件哈希、安全文件读取等基础工具。
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# 文件哈希
# ------------------------------------------------------------------ #

def compute_md5(file_path: str, chunk_size: int = 8192) -> str:
    """计算文件的 MD5 哈希值，用于生成 doc_id。

    Args:
        file_path: 文件路径。
        chunk_size: 每次读取的字节数，默认 8KB。

    Returns:
        32 位十六进制 MD5 字符串。

    Raises:
        FileNotFoundError: 文件不存在。
        PermissionError: 无读取权限。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if not path.is_file():
        raise ValueError(f"不是普通文件: {file_path}")

    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            md5.update(data)
    return md5.hexdigest()


def generate_doc_id(file_path: str) -> str:
    """基于文件 MD5 生成 doc_id。

    格式: "doc_" + MD5 前 12 位。

    Args:
        file_path: 文件路径。

    Returns:
        doc_id 字符串。
    """
    md5_hex = compute_md5(file_path)
    return f"doc_{md5_hex[:12]}"


# ------------------------------------------------------------------ #
# 安全文件读取
# ------------------------------------------------------------------ #

def safe_read_text(file_path: str, encoding: str = "utf-8") -> str:
    """安全读取文本文件内容。

    依次尝试指定编码和 gbk、latin-1，避免解码失败。

    Args:
        file_path: 文件路径。
        encoding: 首选编码，默认 utf-8。

    Returns:
        文件文本内容。

    Raises:
        FileNotFoundError: 文件不存在。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 依次尝试多种编码
    encodings = [encoding, "utf-8-sig", "gbk", "latin-1"]
    last_error: Optional[Exception] = None

    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError as e:
            last_error = e
            continue

    # 所有编码都失败，使用 latin-1（不会抛异常）
    logger.warning("文件 %s 使用 latin-1 兜底读取，可能出现乱码", file_path)
    with open(file_path, "r", encoding="latin-1") as f:
        return f.read()


def safe_read_bytes(file_path: str) -> bytes:
    """安全读取二进制文件内容。

    Args:
        file_path: 文件路径。

    Returns:
        文件二进制内容。

    Raises:
        FileNotFoundError: 文件不存在。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    with open(file_path, "rb") as f:
        return f.read()


def ensure_dir(dir_path: str) -> Path:
    """确保目录存在，不存在则创建。

    Args:
        dir_path: 目录路径。

    Returns:
        Path 对象。
    """
    p = Path(dir_path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_file_extension(file_path: str) -> str:
    """获取文件扩展名（小写，含点号）。

    Args:
        file_path: 文件路径。

    Returns:
        小写扩展名，如 ".pdf"、".docx"。
    """
    return Path(file_path).suffix.lower()
