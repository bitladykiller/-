"""预定义 Cypher 查询的向量匹配工具。

职责：
1. 将查询名称与描述编码为向量
2. 用用户问题匹配最相近的预定义 Cypher
3. 在命中后提取模板参数
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import requests
from app.shared.core.config import settings
from app.shared.core.json_utils import parse_first_json_object
from app.shared.core.logger import get_logger
from numpy.typing import NDArray

logger = get_logger(__name__)

_DEFAULT_EMBEDDING_DIM = 1024
_PARAM_PATTERNS: dict[str, re.Pattern[str]] = {
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
    embedding_dim: int = _DEFAULT_EMBEDDING_DIM,
) -> list[list[float]]:
    """返回固定维度的零向量，避免调用失败时中断后续匹配流程。"""
    return [[0.0] * embedding_dim for _ in range(count)]


def extract_embeddings(
    payload: dict[str, Any],
    *,
    expected_count: int,
    embedding_dim: int = _DEFAULT_EMBEDDING_DIM,
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
        pattern = _PARAM_PATTERNS.get(param_name)
        if pattern is None:
            continue
        match = pattern.search(user_question)
        if match:
            params[param_name] = match.group(1).strip()
    return params


def parse_json_response(content: str) -> dict[str, Any]:
    """从模型返回内容中提取 JSON 对象。"""
    try:
        parsed = parse_first_json_object(content)
        return parsed or {}
    except Exception:
        return {}


def cosine_similarity_score(
    left: NDArray[np.floating[Any]],
    right: NDArray[np.floating[Any]],
) -> float:
    """计算两个向量的余弦相似度。

    WHY 不用 sklearn.metrics.pairwise.cosine_similarity：
    - 传入 list[ndarray] 时静态类型不清晰，Pylance 常报类型错误
    - 零向量（embedding 失败降级）时 sklearn 会给出 nan，这里稳定返回 0.0
    """
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return float(np.dot(left, right) / (left_norm * right_norm))


class _VectorQueryMatcher:
    """基于词向量的预定义 Cypher 查询匹配器。"""

    def __init__(
        self,
        predefined_cypher_dict: dict[str, str],
        query_descriptions: dict[str, str],
        similarity_threshold: float = 0.5,
    ):
        """
        初始化查询匹配器。

        参数:
        predefined_cypher_dict: 预定义的 Cypher 查询字典
        query_descriptions: 每个查询的描述信息字典，用于增强匹配
        similarity_threshold: 相似度阈值，低于该阈值的匹配将被忽略
        """
        self.predefined_cypher_dict = predefined_cypher_dict
        self.query_descriptions = query_descriptions
        self.similarity_threshold = similarity_threshold

        # 使用环境变量获取 Ollama 的基础 URL 和模型名称。
        self.ollama_base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self.ollama_embedding_model = settings.OLLAMA_EMBEDDING_MODEL
        self.ollama_api_url = f"{self.ollama_base_url}/api/embed"

        # 预计算查询向量
        self.query_vectors: dict[str, NDArray[np.floating[Any]]] = {}
        if self.predefined_cypher_dict:
            query_keys, query_texts = build_query_texts(
                self.predefined_cypher_dict,
                self.query_descriptions,
            )
            vectors = self._embed_texts(query_texts)
            self.query_vectors = {
                key: np.asarray(vector, dtype=float)
                for key, vector in zip(query_keys, vectors, strict=True)
            }

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """使用 Ollama embedding API 将文本转换为向量。"""
        try:
            response = requests.post(
                self.ollama_api_url,
                json=build_embed_payload(self.ollama_embedding_model, texts),
                timeout=10,
            )
            response.raise_for_status()
            return extract_embeddings(response.json(), expected_count=len(texts))
        except Exception:
            logger.warning(
                "[cypher_utils] embedding 生成失败，降级为零向量 | texts_count=%s",
                len(texts),
                exc_info=True,
            )
            return fallback_embeddings(len(texts))

    def match_query(self, user_question: str, top_k: int = 3) -> list[dict[str, Any]]:
        """将用户问题匹配到最相似的预定义查询。"""
        if not self.query_vectors:
            return []

        # 对用户问题进行向量化
        question_vector = np.asarray(self._embed_texts([user_question])[0], dtype=float)

        # 计算用户问题与所有预定义查询的相似度
        similarities: list[tuple[str, float]] = []
        for query_name, query_vector in self.query_vectors.items():
            similarity = cosine_similarity_score(question_vector, query_vector)
            similarities.append((query_name, similarity))

        # 按相似度降序排序
        similarities.sort(key=lambda x: x[1], reverse=True)

        # 提取前top_k个结果，但过滤掉低于阈值的匹配
        results: list[dict[str, Any]] = []
        for query_name, similarity in similarities[:top_k]:
            if similarity >= self.similarity_threshold:
                results.append(
                    {
                        "query_name": query_name,
                        "similarity": float(similarity),
                        "cypher": self.predefined_cypher_dict[query_name],
                    }
                )

        return results

    def extract_parameters(
        self,
        user_question: str,
        query_name: str,
        llm=None,
    ) -> dict[str, str]:
        """从用户问题中提取参数。"""
        # 检查查询是否存在
        if query_name not in self.predefined_cypher_dict:
            return {}

        # 获取查询模板
        cypher_template = self.predefined_cypher_dict[query_name]

        # 提取参数列表
        param_names = extract_parameter_names(cypher_template)

        # 使用LLM提取参数（如果提供）
        if llm is not None:
            from langchain_core.prompts import ChatPromptTemplate

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """你是参数提取专家。你的任务是从用户问题中提取指定参数。
            只返回JSON格式的参数值，不要添加任何解释。
            如果无法提取某个参数，则该参数值为空字符串。""",
                    ),
                    (
                        "human",
                        f"""
            用户问题: {user_question}
            查询类型: {query_name}
            需要提取的参数: {', '.join(param_names)}

            请提取这些参数并以JSON格式返回，格式如: {{"参数名": "参数值", ...}}
            """,
                    ),
                ]
            )
            response = llm.invoke(prompt)
            return parse_json_response(getattr(response, "content", ""))

        # 使用简单规则进行参数提取
        return extract_parameters_with_rules(user_question, param_names)


def create_vector_query_matcher(
    predefined_cypher_dict: dict[str, str],
    query_descriptions: dict[str, str] | None = None,
) -> _VectorQueryMatcher:
    """
    创建并返回预定义 Cypher 查询匹配器实例。

    参数:
    predefined_cypher_dict: 预定义的 Cypher 查询字典
    query_descriptions: 可选的查询描述字典

    返回:
    查询匹配器实例
    """
    # 如果没有提供描述，为每个查询生成默认描述。
    if query_descriptions is None:
        query_descriptions = {
            query_name: query_name.replace("_", " ")
            for query_name in predefined_cypher_dict
        }

    return _VectorQueryMatcher(predefined_cypher_dict, query_descriptions)
