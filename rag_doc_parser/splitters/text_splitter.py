"""
RAG 文档解析器 — 文本切分器。

递归切分文本，支持多级分隔符和重叠。
分隔符优先级：\\n\\n > \\n > 。> ！> ？> ；> ，> 空格 > 硬切。
"""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


class TextSplitter:
    """递归文本切分器。

    按照优先级依次尝试多种分隔符切分文本，确保每个 chunk 不超过指定大小。
    支持 chunk 之间的重叠（overlap），避免语义断裂。

    分隔符优先级（从高到低）：
    1. 段落分隔：\\n\\n
    2. 换行：\\n
    3. 句号：。
    4. 感叹号：！
    5. 问号：？
    6. 分号：；
    7. 逗号：，
    8. 空格
    9. 硬切（按字符数截断）

    Attributes:
        chunk_size: 每个 chunk 的最大字符数。
        chunk_overlap: 相邻 chunk 的重叠字符数。
    """

    # 分隔符优先级列表
    SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " "]

    def __init__(
        self,
        chunk_size: int = 700,
        chunk_overlap: int = 100,
    ) -> None:
        """初始化文本切分器。

        Args:
            chunk_size: 每个 chunk 的最大字符数，默认 700。
            chunk_overlap: 相邻 chunk 的重叠字符数，默认 100。

        Raises:
            ValueError: chunk_size <= 0 或 overlap 不合法。
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须 > 0")
        if not (0 <= chunk_overlap < chunk_size):
            raise ValueError("chunk_overlap 必须满足 0 <= overlap < chunk_size")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str) -> List[str]:
        """递归切分文本。

        Args:
            text: 待切分的文本。

        Returns:
            切分后的文本片段列表。
        """
        if not text or not text.strip():
            return []

        text = text.strip()

        # 如果文本长度 <= chunk_size，直接返回
        if len(text) <= self.chunk_size:
            return [text]

        # 递归切分
        chunks = self._recursive_split(text, self.SEPARATORS)

        # 应用重叠
        if self.chunk_overlap > 0:
            chunks = self._apply_overlap(chunks)

        return chunks

    def _recursive_split(self, text: str, separators: List[str]) -> List[str]:
        """递归切分文本。

        Args:
            text: 待切分文本。
            separators: 当前可用的分隔符列表。

        Returns:
            切分后的文本片段列表。
        """
        # 如果文本足够短，直接返回
        if len(text) <= self.chunk_size:
            return [text]

        # 尝试每种分隔符
        for i, sep in enumerate(separators):
            if sep not in text:
                continue

            # 用当前分隔符切分
            parts = text.split(sep)

            # 如果只切出 1 个部分，说明分隔符不存在或在开头结尾
            if len(parts) <= 1:
                continue

            # 合并小块，确保每块不超过 chunk_size
            merged_chunks = self._merge_small_chunks(parts, sep)

            # 检查是否有超大块需要进一步切分
            result: List[str] = []
            for chunk in merged_chunks:
                if len(chunk) > self.chunk_size:
                    # 用下一级分隔符继续切分
                    sub_separators = separators[i + 1:] if i + 1 < len(separators) else []
                    if sub_separators:
                        result.extend(self._recursive_split(chunk, sub_separators))
                    else:
                        # 所有分隔符都用完了，硬切
                        result.extend(self._hard_split(chunk))
                else:
                    result.append(chunk)

            return result

        # 所有分隔符都不适用，硬切
        return self._hard_split(text)

    def _merge_small_chunks(self, parts: List[str], separator: str) -> List[str]:
        """合并小块，确保每块不超过 chunk_size。

        Args:
            parts: 分隔符切分后的各部分。
            separator: 使用的分隔符。

        Returns:
            合并后的文本块列表。
        """
        merged: List[str] = []
        current = ""

        for part in parts:
            candidate = part.strip()
            if not candidate:
                continue

            # 如果当前块加上新部分不超限
            if current:
                test = current + separator + candidate
            else:
                test = candidate

            if len(test) <= self.chunk_size:
                current = test
            else:
                # 保存当前块
                if current:
                    merged.append(current)
                current = candidate

        # 保存最后一块
        if current:
            merged.append(current)

        return merged

    def _hard_split(self, text: str) -> List[str]:
        """硬切：按 chunk_size 截断。

        当所有分隔符都不适用时，直接按字符数截断。

        Args:
            text: 待切分文本。

        Returns:
            切分后的文本块列表。
        """
        chunks: List[str] = []
        start = 0

        while start < len(text):
            end = start + self.chunk_size
            chunks.append(text[start:end])
            start = end

        return chunks

    def _apply_overlap(self, chunks: List[str]) -> List[str]:
        """在相邻 chunk 之间添加重叠。

        每个 chunk（除了第一个）的开头包含上一个 chunk 末尾的 overlap 个字符。

        Args:
            chunks: 原始切分结果。

        Returns:
            添加重叠后的切分结果。
        """
        if len(chunks) <= 1 or self.chunk_overlap <= 0:
            return chunks

        result: List[str] = [chunks[0]]

        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            current = chunks[i]

            # 取上一个 chunk 末尾的 overlap 个字符
            overlap_text = prev[-self.chunk_overlap:]

            # 将重叠文本添加到当前 chunk 开头
            combined = overlap_text + current

            # 如果合并后超过 chunk_size，截断
            if len(combined) > self.chunk_size:
                combined = combined[:self.chunk_size]

            result.append(combined)

        return result
