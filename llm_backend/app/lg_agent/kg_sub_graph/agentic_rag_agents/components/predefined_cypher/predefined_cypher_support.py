"""预定义 Cypher 匹配层的纯 helper。

这个模块负责：
- 构造 embedding 请求体与零向量降级结果
- 生成查询文本、提取模板参数、解析 LLM 返回 JSON
- 承接不依赖网络 I/O 的纯文本处理逻辑

这个模块不负责：
- 调用 Ollama embedding API
- 计算相似度并筛选最终模板
- 管理 VectorQueryMatcher 的运行时状态
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_EMBEDDING_DIM = 1024
PARAM_PATTERNS: dict[str, re.Pattern[str]] = {
    "product_name": re.compile(
        r"(?:关于|查询|找|有关)\s*([\w\s\u4e00-\u9fff-]+?)\s*(?:的|是|多少)"
    ),
    "category_name": re.compile(
        r"(?:类别|分类|种类|类型)\s*([\w\s\u4e00-\u9fff-]+?)\s*(?:的|是|有)"
    ),
    "order_id": re.compile(r"订单\s*([0-9]+)"),
}


def build_embed_payload(model: str, texts: list[str]) -> dict[str, Any]:
    """构造 Ollama embedding 请求体。"""
    return {
        "model": model,
        "input": texts,
    }


def fallback_embeddings(
    count: int,
    *,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
) -> list[list[float]]:
    """返回固定维度的零向量，避免调用失败时中断后续匹配流程。"""
    return [[0.0] * embedding_dim for _ in range(count)]


def extract_embeddings(
    payload: dict[str, Any],
    *,
    expected_count: int,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
) -> list[list[float]]:
    """从 Ollama 响应中提取 `embeddings` 字段。"""
    embeddings = payload.get("embeddings", [])
    if isinstance(embeddings, list) and embeddings:
        return embeddings
    return fallback_embeddings(expected_count, embedding_dim=embedding_dim)


def build_query_texts(
    predefined_cypher_dict: Mapping[str, str],
    query_descriptions: Mapping[str, str],
) -> tuple[list[str], list[str]]:
    """把查询名和描述拼成 embedding 输入文本。"""
    query_texts: list[str] = []
    query_keys: list[str] = []
    for query_name in predefined_cypher_dict:
        description = query_descriptions.get(query_name, "")
        query_texts.append(f"{query_name} {description}".strip())
        query_keys.append(query_name)
    return query_keys, query_texts


def extract_parameter_names(cypher_template: str) -> list[str]:
    """提取 Cypher 模板中的参数名。"""
    return re.findall(r"\$(\w+)", cypher_template)


def extract_parameters_with_rules(
    user_question: str,
    param_names: Sequence[str],
) -> dict[str, str]:
    """使用规则从用户问题中提取参数。"""
    params: dict[str, str] = {}
    for param_name in param_names:
        pattern = PARAM_PATTERNS.get(param_name)
        if pattern is None:
            continue
        match = pattern.search(user_question)
        if match:
            params[param_name] = match.group(1).strip()
    return params


def extract_first_json_object(content: str) -> str | None:
    """提取首个完整 JSON 对象，避免贪婪正则跨越额外文本。"""
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
                return content[start:index + 1]

    return None


def parse_json_response(content: str) -> dict[str, Any]:
    """从模型返回内容中提取 JSON 对象。"""
    try:
        payload = extract_first_json_object(content)
        if payload is None:
            return {}
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def build_default_query_descriptions(
    predefined_cypher_dict: Mapping[str, str],
) -> dict[str, str]:
    """为缺失描述的模板生成可读的默认说明。"""
    return {
        query_name: query_name.replace("_", " ")
        for query_name in predefined_cypher_dict
    }
