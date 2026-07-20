"""
RAG 文档解析器 — Markdown 内容块解析器。

将 MarkdownSection 的 content 拆分为多个 MarkdownBlock。
识别代码块、表格、图片说明、普通文本等类型。
"""

from __future__ import annotations

import re
from typing import Literal

from app.knowledge.infrastructure.doc_parser.markdown.table_utils import is_markdown_table
from app.knowledge.infrastructure.doc_parser.models import MarkdownBlock, MarkdownSection, new_uuid

# 代码块开始标记：```language
_CODE_FENCE_START = re.compile(r"^```(\w*)\s*$")
# 代码块结束标记：```
_CODE_FENCE_END = re.compile(r"^```\s*$")
# 图片标记：![alt](url)
_IMAGE_RE = re.compile(r"^!\[.*?\]\(.*?\)")
# 图片说明标记：**图片...** 或 **描述：**
_IMAGE_CAPTION_RE = re.compile(r"^\*\*(图片|描述|标题|分类)[：:]")
_IMAGE_CAPTION_BLOCK_START_RE = re.compile(r"^:::image_caption\s*$")
_IMAGE_CAPTION_BLOCK_END_RE = re.compile(r"^:::\s*$")


class BlockParser:
    """Markdown 内容块解析器。

    将 MarkdownSection 的 content 拆分为多个 MarkdownBlock，
    识别以下类型：
    - code: 代码块（``` 包裹）
    - table: Markdown 表格（| 分隔的行）
    - image_caption: 图片说明（来自 VLM 描述）
    - text: 普通文本段落

    输出的 MarkdownBlock 继承 section 的标题信息。
    """

    def parse(self, section: MarkdownSection) -> list[MarkdownBlock]:
        """将 section 的 content 拆分为 MarkdownBlock 列表。

        Args:
            section: MarkdownSection 实例。

        Returns:
            MarkdownBlock 列表。
        """
        content = section.content
        if not content or not content.strip():
            return []

        lines = content.split("\n")
        blocks: list[MarkdownBlock] = []

        # 状态机
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 空行跳过
            if not stripped:
                i += 1
                continue

            # 检测代码块开始
            code_match = _CODE_FENCE_START.match(stripped)
            if code_match:
                language = code_match.group(1) or None
                code_lines, end_idx = self._extract_code_block(lines, i)
                block = self._make_block(
                    block_type="code",
                    content="\n".join(code_lines),
                    section=section,
                    extra_metadata={"language": language} if language else {},
                )
                # 为代码块设置 language 属性
                block.metadata["language"] = language
                blocks.append(block)
                i = end_idx + 1
                continue

            # 检测表格
            if is_markdown_table(stripped):
                table_lines, end_idx = self._extract_table(lines, i)
                block = self._make_block(
                    block_type="table",
                    content="\n".join(table_lines),
                    section=section,
                )
                blocks.append(block)
                i = end_idx + 1
                continue

            # 检测图片说明（VLM 生成的描述）
            if (
                _IMAGE_CAPTION_RE.match(stripped)
                or _IMAGE_RE.match(stripped)
                or _IMAGE_CAPTION_BLOCK_START_RE.match(stripped)
            ):
                caption_lines, end_idx = self._extract_image_caption(lines, i)
                block = self._make_block(
                    block_type="image_caption",
                    content="\n".join(caption_lines),
                    section=section,
                )
                blocks.append(block)
                i = end_idx + 1
                continue

            # 普通文本：累积连续非空、非代码、非表格、非图片行
            text_lines, end_idx = self._extract_text(lines, i)
            block = self._make_block(
                block_type="text",
                content="\n".join(text_lines),
                section=section,
            )
            blocks.append(block)
            i = end_idx + 1

        return blocks

    def _extract_code_block(
        self, lines: list[str], start: int
    ) -> tuple[list[str], int]:
        """提取代码块内容（不含开始/结束标记行）。

        Args:
            lines: 全部行。
            start: 代码块开始行索引。

        Returns:
            (代码行列表, 结束行索引)。
        """
        code_lines: list[str] = []
        i = start + 1  # 跳过开始标记

        while i < len(lines):
            if _CODE_FENCE_END.match(lines[i].strip()):
                return code_lines, i
            code_lines.append(lines[i])
            i += 1

        # 没有找到结束标记，取到末尾
        return code_lines, len(lines) - 1

    def _extract_table(
        self, lines: list[str], start: int
    ) -> tuple[list[str], int]:
        """提取表格行。

        Args:
            lines: 全部行。
            start: 表格开始行索引。

        Returns:
            (表格行列表, 结束行索引)。
        """
        table_lines: list[str] = []
        i = start

        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped or not is_markdown_table(stripped):
                break
            table_lines.append(lines[i])
            i += 1

        return table_lines, i - 1

    def _extract_image_caption(
        self, lines: list[str], start: int
    ) -> tuple[list[str], int]:
        """提取图片说明行。

        图片说明通常是连续的以 ** 开头的行。

        Args:
            lines: 全部行。
            start: 图片说明开始行索引。

        Returns:
            (图片说明行列表, 结束行索引)。
        """
        caption_lines: list[str] = []
        i = start

        inside_caption_block = False

        while i < len(lines):
            stripped = lines[i].strip()
            if _IMAGE_CAPTION_BLOCK_START_RE.match(stripped):
                inside_caption_block = True
                caption_lines.append(lines[i])
                i += 1
                continue

            if inside_caption_block:
                caption_lines.append(lines[i])
                if _IMAGE_CAPTION_BLOCK_END_RE.match(stripped):
                    inside_caption_block = False
                i += 1
                continue

            # 图片说明行：以 ** 开头、Markdown 图片行，或空行分隔
            if (
                _IMAGE_CAPTION_RE.match(stripped)
                or _IMAGE_RE.match(stripped)
                or stripped.startswith("**")
            ):
                caption_lines.append(lines[i])
                i += 1
            elif not stripped:
                caption_lines.append(lines[i])
                i += 1
            else:
                break

        # 去除尾部空行
        while caption_lines and not caption_lines[-1].strip():
            caption_lines.pop()

        return caption_lines, i - 1

    def _extract_text(
        self, lines: list[str], start: int
    ) -> tuple[list[str], int]:
        """提取连续的普通文本行。

        遇到代码块标记、表格行、图片说明行时停止。

        Args:
            lines: 全部行。
            start: 文本开始行索引。

        Returns:
            (文本行列表, 结束行索引)。
        """
        text_lines: list[str] = []
        i = start

        while i < len(lines):
            stripped = lines[i].strip()

            # 遇到特殊块类型则停止
            if not stripped:
                break
            if _CODE_FENCE_START.match(stripped):
                break
            if is_markdown_table(stripped):
                break
            if (
                _IMAGE_CAPTION_RE.match(stripped)
                or _IMAGE_RE.match(stripped)
                or _IMAGE_CAPTION_BLOCK_START_RE.match(stripped)
            ):
                break

            text_lines.append(lines[i])
            i += 1

        return text_lines, i - 1

    @staticmethod
    def _make_block(
        block_type: Literal["text", "table", "code", "image_caption"],
        content: str,
        section: MarkdownSection,
        extra_metadata: dict[str, object] | None = None,
    ) -> MarkdownBlock:
        """创建 MarkdownBlock，继承 section 的标题信息。

        Args:
            block_type: 块类型。
            content: 块内容。
            section: 所属 section。
            extra_metadata: 额外元数据。

        Returns:
            MarkdownBlock 实例。
        """
        metadata: dict[str, object] = {}
        if extra_metadata:
            metadata.update(extra_metadata)

        return MarkdownBlock(
            block_id=new_uuid(),
            block_type=block_type,
            content=content,
            section_path=section.section_path,
            h1=section.h1,
            h2=section.h2,
            h3=section.h3,
            h4=section.h4,
            page_start=section.page_start,
            page_end=section.page_end,
            metadata=metadata,
        )
