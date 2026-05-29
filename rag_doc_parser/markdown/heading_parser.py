"""
RAG 文档解析器 — Markdown 标题解析器。

解析 Markdown 中的 # 标题，构建多级章节结构。
支持 1-4 级标题，维护标题状态，生成 section_path。
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from rag_doc_parser.exceptions import MarkdownParseError
from rag_doc_parser.models import MarkdownSection, new_uuid

logger = logging.getLogger(__name__)

# 标题正则：匹配 1-4 个 # 开头的行
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")


class HeadingParser:
    """Markdown 标题解析器。

    将 Markdown 全文按标题拆分为多个 MarkdownSection。
    维护 h1-h4 状态机，为每个 section 生成完整的 section_path。

    特性：
    - 支持 1-4 级标题。
    - 标题之前的正文（pre-title content）使用默认标题。
    - section_path 以 " > " 连接各级标题。
    """

    def __init__(self, default_title: str = "Untitled") -> None:
        """初始化标题解析器。

        Args:
            default_title: 无标题内容的默认标题。
        """
        self.default_title = default_title

    def parse(self, markdown: str) -> List[MarkdownSection]:
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

        Raises:
            MarkdownParseError: 解析出错时抛出。
        """
        if not markdown:
            return []

        lines = markdown.split("\n")

        # 当前标题状态
        current_h1: Optional[str] = None
        current_h2: Optional[str] = None
        current_h3: Optional[str] = None
        current_h4: Optional[str] = None
        current_level: int = 0
        current_title: str = ""

        # 内容积累
        content_lines: List[str] = []
        sections: List[MarkdownSection] = []

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
                        title=current_title,
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
                current_title = title_text

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
                title=current_title,
                content_lines=content_lines,
            )
            sections.append(section)

        # 如果整个文档没有标题，创建一个默认 section
        if not has_seen_heading and content_lines:
            section = MarkdownSection(
                section_id=new_uuid(),
                level=0,
                title=self.default_title,
                section_path=self.default_title,
                content="\n".join(content_lines).strip(),
            )
            sections = [section]

        return sections

    def _build_section(
        self,
        h1: Optional[str],
        h2: Optional[str],
        h3: Optional[str],
        h4: Optional[str],
        level: int,
        title: str,
        content_lines: List[str],
    ) -> MarkdownSection:
        """构建一个 MarkdownSection。

        Args:
            h1-h4: 各级当前标题。
            level: 当前标题级别。
            title: 当前标题文本。
            content_lines: 内容行列表。

        Returns:
            MarkdownSection 实例。
        """
        # 如果没有标题（pre-title 内容），使用默认标题
        if not title:
            title = self.default_title
            level = 0

        # 构建 section_path
        section_path = self._build_section_path(h1, h2, h3, h4, level)

        # 合并内容
        content = "\n".join(content_lines).strip()

        return MarkdownSection(
            section_id=new_uuid(),
            level=level,
            title=title,
            section_path=section_path,
            h1=h1,
            h2=h2,
            h3=h3,
            h4=h4,
            content=content,
        )

    @staticmethod
    def _build_section_path(
        h1: Optional[str],
        h2: Optional[str],
        h3: Optional[str],
        h4: Optional[str],
        level: int,
    ) -> str:
        """构建 section_path，用 " > " 连接各级标题。

        例如：h1="数据库", h2="事务管理", h3="隔离级别"
        → section_path = "数据库 > 事务管理 > 隔离级别"

        Args:
            h1-h4: 各级标题。
            level: 当前标题级别。

        Returns:
            完整的 section_path 字符串。
        """
        parts: List[str] = []

        # 根据当前级别，决定 section_path 包含哪些层级
        if h1 and level >= 1:
            parts.append(h1)
        if h2 and level >= 2:
            parts.append(h2)
        if h3 and level >= 3:
            parts.append(h3)
        if h4 and level >= 4:
            parts.append(h4)

        return " > ".join(parts) if parts else "Untitled"
