"""JSON 文本辅助函数。"""

from __future__ import annotations


def extract_first_json_object(content: str) -> str | None:
    """提取首个完整 JSON 对象，避免额外文本或字符串里的大括号干扰。"""
    start = content.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(content)):
        char = content[index]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]

    return None
