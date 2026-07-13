"""
RAG 文档解析器 — Markdown 清洗器。

对原始 Markdown 进行规范化处理：
- 统一换行符
- 合并多余空行
- 移除页码行
- 去除行尾空白
- 保护代码块和表格不被破坏
"""

from __future__ import annotations

import re


class MarkdownCleaner:
    """Markdown 文本清洗器。

    在不破坏代码块和表格结构的前提下，对 Markdown 进行规范化。

    注意：所有清洗操作都会先识别代码块区域，对代码块内部不做修改。
    """

    # 页码模式：匹配单独一行的数字（如 "1"、"12"、"第 3 页"、"Page 5"）
    _PAGE_NUMBER_PATTERNS = [
        re.compile(r"^\s*\d{1,4}\s*$"),                          # 纯数字
        re.compile(r"^\s*第\s*\d+\s*页\s*$"),                    # 第 X 页
        re.compile(r"^\s*Page\s+\d+\s*$", re.IGNORECASE),       # Page X
        re.compile(r"^\s*-\s*\d+\s*-\s*$"),                      # - X -
    ]

    def clean(self, markdown: str) -> str:
        """清洗 Markdown 文本。

        主流程：
        1. 统一换行符为 \\n
        2. 去除行尾空白（代码块内除外）
        3. 移除页码行（代码块内除外）
        4. 合并连续 3 个以上空行为 2 个

        Args:
            markdown: 原始 Markdown 文本。

        Returns:
            清洗后的 Markdown 文本。
        """
        if not markdown:
            return ""

        # 1. 统一换行符
        text = markdown.replace("\r\n", "\n").replace("\r", "\n")

        # 2. 分割为行
        lines = text.split("\n")

        # 3. 识别代码块区域，标记哪些行在代码块内
        in_code_block = self._mark_code_blocks(lines)

        # 4. 逐行处理
        cleaned_lines: list[str] = []
        for i, line in enumerate(lines):
            # 在代码块内的行不做任何修改
            if in_code_block[i]:
                cleaned_lines.append(line)
                continue

            # 去除行尾空白
            stripped = line.rstrip()

            # 移除页码行
            if self._is_page_number(stripped):
                continue

            cleaned_lines.append(stripped)

        # 5. 合并多余空行
        result = self._collapse_blank_lines(cleaned_lines)

        return result

    @staticmethod
    def _mark_code_blocks(lines: list[str]) -> list[bool]:
        """标记每一行是否在代码块（``` ... ```）内部。

        Args:
            lines: Markdown 文本行列表。

        Returns:
            与 lines 等长的布尔列表，True 表示该行在代码块内。
        """
        in_code = [False] * len(lines)
        inside = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            # 检测代码块标记行：以 ``` 开头
            if stripped.startswith("```"):
                in_code[i] = True  # 标记行本身也属于代码块
                inside = not inside
            elif inside:
                in_code[i] = True

        return in_code

    def _is_page_number(self, line: str) -> bool:
        """判断一行是否为页码。

        Args:
            line: 已去除行尾空白的文本行。

        Returns:
            是否为页码行。
        """
        if not line:
            return False
        for pattern in self._PAGE_NUMBER_PATTERNS:
            if pattern.match(line):
                return True
        return False

    @staticmethod
    def _collapse_blank_lines(lines: list[str]) -> str:
        """合并连续 3 个以上空行为 2 个空行。

        Args:
            lines: 处理后的行列表。

        Returns:
            合并空行后的完整文本。
        """
        result_parts: list[str] = []
        blank_count = 0

        for line in lines:
            if line.strip() == "":
                blank_count += 1
                # 最多保留 2 个连续空行
                if blank_count <= 2:
                    result_parts.append(line)
            else:
                blank_count = 0
                result_parts.append(line)

        # 去除首尾空行
        while result_parts and result_parts[0].strip() == "":
            result_parts.pop(0)
        while result_parts and result_parts[-1].strip() == "":
            result_parts.pop()

        return "\n".join(result_parts)
