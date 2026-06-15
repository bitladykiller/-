"""
RAG 文档解析器 — Markdown 表格工具。

提供表格识别、解析和构建功能。
"""

import re
from typing import List, Tuple


# 表格行正则：以 | 开头和结尾，中间有 | 分隔
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
# 分隔行：| --- | --- | ... 或 | :---: | ---: | ... 等
_SEPARATOR_RE = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+\s*$")


def is_markdown_table(line: str) -> bool:
    """判断一行是否是 Markdown 表格行。

    表格行的特征：
    - 以 | 开头和结尾
    - 中间用 | 分隔单元格

    Args:
        line: 单行文本（已去除行尾空白）。

    Returns:
        是否为表格行。
    """
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    # 至少有 2 个 |（开头和结尾各一个，中间至少一个分隔）
    if stripped.count("|") < 3:
        return False
    return True


def parse_markdown_table(
    table_text: str,
) -> Tuple[List[str], List[List[str]]]:
    """解析 Markdown 表格为表头和数据行。

    输入格式：
    | 列1 | 列2 | 列3 |
    | --- | --- | --- |
    | 数据1 | 数据2 | 数据3 |

    Args:
        table_text: Markdown 表格文本。

    Returns:
        (headers, rows) 元组。
        headers: 表头列表。
        rows: 数据行列表，每行是单元格值列表。

    示例：
        >>> text = "| A | B |\\n| --- | --- |\\n| 1 | 2 |"
        >>> headers, rows = parse_markdown_table(text)
        >>> headers
        ['A', 'B']
        >>> rows
        [['1', '2']]
    """
    lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]

    if len(lines) < 2:
        return [], []

    headers: List[str] = []
    rows: List[List[str]] = []
    separator_found = False

    for line in lines:
        if not is_markdown_table(line):
            continue

        # 提取单元格内容
        match = _TABLE_ROW_RE.match(line)
        if not match:
            continue

        cells_str = match.group(1)
        cells = [c.strip() for c in cells_str.split("|")]

        # 检查是否为分隔行
        if _SEPARATOR_RE.match(line):
            separator_found = True
            continue

        # 分隔行之前的是表头，之后的是数据
        if not separator_found and not headers:
            headers = cells
        elif separator_found:
            rows.append(cells)

    return headers, rows


def build_markdown_table(
    headers: List[str],
    rows: List[List[str]],
) -> str:
    """构建 Markdown 表格文本。

    Args:
        headers: 表头列表。
        rows: 数据行列表。

    Returns:
        Markdown 格式的表格文本。

    示例：
        >>> build_markdown_table(["A", "B"], [["1", "2"], ["3", "4"]])
        '| A | B |\\n| --- | --- |\\n| 1 | 2 |\\n| 3 | 4 |'
    """
    if not headers:
        return ""

    # 确定列数
    col_count = len(headers)

    lines: List[str] = []

    # 表头行
    lines.append("| " + " | ".join(headers) + " |")

    # 分隔行
    lines.append("| " + " | ".join(["---"] * col_count) + " |")

    # 数据行
    for row in rows:
        # 补齐或截断列数
        padded_row = row[:col_count]
        while len(padded_row) < col_count:
            padded_row.append("")
        lines.append("| " + " | ".join(padded_row) + " |")

    return "\n".join(lines)
