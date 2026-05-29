"""
RAG 文档解析器 — 代码块切分器（按函数/类边界优先）。

长代码块优先按语言特定的类/函数边界切分，保持逻辑完整。
超长函数退回到行级硬切分。
"""

from __future__ import annotations

import re
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# 各语言的函数/类边界关键词
_BOUNDARY_KEYWORDS = {
    "python":    ("def ", "class ", "async def "),
    "java":      ("public class ", "public void ", "public static ", "private ", "protected "),
    "javascript": ("function ", "class ", "async function ", "export function ", "export class "),
    "typescript": ("function ", "class ", "async function ", "export ", "interface "),
    "go":        ("func ", "type ", "const (", "var ("),
    "rust":      ("fn ", "pub fn ", "impl ", "struct ", "enum ", "trait "),
}


class CodeSplitter:
    """代码块切分器。

    优先按函数/类边界切分，保持代码逻辑完整性。
    超长函数退回到行级硬切分。
    """

    def __init__(self, max_lines_per_chunk: int = 120) -> None:
        self.max_lines_per_chunk = max_lines_per_chunk

    def split(self, code_block: str, language: str = "") -> List[str]:
        """切分代码块。

        Args:
            code_block: 含 ``` 标记的 Markdown 代码块。
            language: 代码语言。

        Returns:
            切分后的代码块列表。
        """
        lines = code_block.split("\n")
        lang = language or CodeSplitter._detect_lang(lines)

        code_lines, fence_start = CodeSplitter._strip_fences(lines)
        n = len(code_lines)
        if n <= self.max_lines_per_chunk:
            return [CodeSplitter._wrap(code_lines, lang)] if code_lines else []

        # 1. 按函数/类边界切分
        chunks = CodeSplitter._split_by_functions(code_lines, lang)
        if len(chunks) > 1:
            return [CodeSplitter._wrap(c, lang) for c in chunks if c]

        # 2. 退回到行级切分
        result = []
        for i in range(0, n, self.max_lines_per_chunk):
            sub = code_lines[i : i + self.max_lines_per_chunk]
            result.append(CodeSplitter._wrap(sub, lang))
        return result

    # ------------------------------------------------------------------ #
    # 函数边界切分
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_by_functions(lines: List[str], lang: str) -> List[List[str]]:
        """识别所有函数/类边界，按边界切分。

        算法:
        1. 找到每一行的缩进级别
        2. 找到顶层（缩进 0）的函数/类声明行
        3. 以这些行为边界切分
        4. 每个边界行到下一个边界行之间的内容作为一个 chunk
        """
        keywords = _BOUNDARY_KEYWORDS.get(lang)
        if not keywords:
            return []

        # 计算每行的缩进级别
        indents = [len(line) - len(line.lstrip()) for line in lines]

        # 找到顶层函数/类声明 (缩进 0-2 且匹配关键词)
        boundaries = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//", "/*", "*", "'''", '"""', "@")):
                continue
            if indents[i] <= 2 and any(stripped.startswith(kw) for kw in keywords):
                boundaries.append(i)

        if len(boundaries) < 2:
            return []

        # 按边界切分
        chunks = []
        for j in range(len(boundaries) - 1):
            start = boundaries[j]
            end = boundaries[j + 1]
            chunk = lines[start:end]
            if chunk:
                chunks.append(chunk)

        # 最后一个函数到末尾
        last_chunk = lines[boundaries[-1]:]
        if last_chunk:
            chunks.append(last_chunk)

        # 处理第一个函数之前的导入等
        if boundaries[0] > 0:
            header = lines[: boundaries[0]]
            header_clean = [l for l in header if l.strip()]
            if header_clean:
                chunks.insert(0, header_clean)

        return chunks

    # ------------------------------------------------------------------ #
    # 工具方法
    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_lang(lines: List[str]) -> str:
        """从 ```python 提取语言标记。"""
        if lines and lines[0].strip().startswith("```"):
            return lines[0].strip()[3:].strip().lower()
        return ""

    @staticmethod
    def _strip_fences(lines: List[str]) -> tuple:
        """去除首尾 ``` 标记。"""
        start = 1 if lines and lines[0].strip().startswith("```") else 0
        end = -1 if lines and lines[-1].strip() == "```" else len(lines)
        return lines[start:end], start

    @staticmethod
    def _wrap(code_lines: List[str], language: str) -> str:
        h = f"```{language}" if language else "```"
        return h + "\n" + "\n".join(code_lines) + "\n```"
