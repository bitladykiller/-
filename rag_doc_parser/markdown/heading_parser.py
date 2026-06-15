"""
RAG 文档解析器 — Markdown 标题解析器。

解析 Markdown 中的 # 标题，构建多级章节结构。
支持 1-4 级标题，维护标题状态，生成 section_path。
"""

import re

from rag_doc_parser.models import MarkdownSection

# 标题正则：匹配 1-4 个 # 开头的行
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_DEFAULT_TITLE = "Untitled"


class HeadingParser:
    """Markdown 标题解析器。

    将 Markdown 全文按标题拆分为多个 MarkdownSection。
    维护 h1-h4 状态机，为每个 section 生成完整的 section_path。

    特性：
    - 支持 1-4 级标题。
    - 标题之前的正文（pre-title content）使用默认标题。
    - section_path 以 " > " 连接各级标题。
    """

    def parse(self, markdown: str) -> list[MarkdownSection]:
        """将 Markdown 按标题拆分为章节列表。

        算法：
        1. 逐行扫描，识别标题行。
        2. 遇到新标题时，将之前积累的内容保存为一个 section。
        3. 维护 h1-h4 状态，低级别标题会清除更低级别的状态。
        4. 标题之前的内容（pre-title）使用默认标题。

        Args:
            markdown: 清洗后的 Markdown 文本。

        Returns:
            MarkdownSection 列表。

        """
        if not markdown:
            return []

        lines = markdown.split("\n")

        # 当前标题状态
        current_h1: str | None = None
        current_h2: str | None = None
        current_h3: str | None = None
        current_h4: str | None = None
        current_level: int = 0

        # 内容积累
        content_lines: list[str] = []
        sections: list[MarkdownSection] = []

        # 是否已遇到第一个标题
        has_seen_heading = False

        for line in lines:
            match = _HEADING_RE.match(line.strip())

            if match:
                # 遇到标题行，先保存之前积累的内容
                if content_lines or has_seen_heading:
                    section = self._build_section(
                        h1=current_h1,
                        h2=current_h2,
                        h3=current_h3,
                        h4=current_h4,
                        level=current_level,
                        content_lines=content_lines,
                    )
                    sections.append(section)
                    content_lines = []

                # 更新标题状态
                level = len(match.group(1))
                title_text = match.group(2).strip()
                has_seen_heading = True

                # 更新各级标题状态
                if level == 1:
                    current_h1 = title_text
                    current_h2 = None
                    current_h3 = None
                    current_h4 = None
                elif level == 2:
                    current_h2 = title_text
                    current_h3 = None
                    current_h4 = None
                elif level == 3:
                    current_h3 = title_text
                    current_h4 = None
                elif level == 4:
                    current_h4 = title_text

                current_level = level

            else:
                # 普通内容行，积累
                content_lines.append(line)

        # 保存最后一段内容
        if content_lines or has_seen_heading:
            section = self._build_section(
                h1=current_h1,
                h2=current_h2,
                h3=current_h3,
                h4=current_h4,
                level=current_level,
                content_lines=content_lines,
            )
            sections.append(section)

        return sections

    def _build_section(
        self,
        h1: str | None,
        h2: str | None,
        h3: str | None,
        h4: str | None,
        level: int,
        content_lines: list[str],
    ) -> MarkdownSection:
        """构建一个 MarkdownSection。

        Args:
            h1-h4: 各级当前标题。
            level: 当前标题级别。
            content_lines: 内容行列表。

        Returns:
            MarkdownSection 实例。
        """
        # 构建 section_path
        parts: list[str] = []
        if h1 and level >= 1:
            parts.append(h1)
        if h2 and level >= 2:
            parts.append(h2)
        if h3 and level >= 3:
            parts.append(h3)
        if h4 and level >= 4:
            parts.append(h4)
        section_path = " > ".join(parts) if parts else _DEFAULT_TITLE

        # 合并内容
        content = "\n".join(content_lines).strip()

        return MarkdownSection(
            section_path=section_path,
            content=content,
        )
