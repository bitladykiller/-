"""预定义 Cypher 查询的向量匹配工具。

职责：
1. 将查询名称与描述编码为向量
2. 用用户问题匹配最相近的预定义 Cypher
3. 在命中后提取模板参数
"""

from __future__ import annotations

from typing import Any

import numpy as np
import requests
from sklearn.metrics.pairwise import cosine_similarity

from app.core.config import settings
from .predefined_cypher_support import (
    build_default_query_descriptions,
    build_embed_payload,
    build_query_texts,
    extract_embeddings,
    extract_parameter_names,
    extract_parameters_with_rules,
    fallback_embeddings,
    parse_json_response,
)


class VectorQueryMatcher:
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
        self.query_vectors = self._compute_query_vectors()

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """使用 Ollama embedding API 将文本转换为向量。"""
        try:
            response = requests.post(
                self.ollama_api_url,
                json=self._build_embed_payload(texts),
                timeout=10,
            )
            response.raise_for_status()
            return self._extract_embeddings(response.json(), expected_count=len(texts))
        except Exception:
            return self._fallback_embeddings(len(texts))

    def _build_embed_payload(self, texts: list[str]) -> dict[str, Any]:
        """构造 Ollama embedding 请求体。"""
        return build_embed_payload(self.ollama_embedding_model, texts)

    def _extract_embeddings(
        self,
        payload: dict[str, Any],
        *,
        expected_count: int,
    ) -> list[list[float]]:
        """从 Ollama 响应中提取 embeddings 字段。"""
        return extract_embeddings(payload, expected_count=expected_count)

    def _fallback_embeddings(self, count: int) -> list[list[float]]:
        """返回固定维度的零向量，避免调用失败时中断后续匹配流程。"""
        return fallback_embeddings(count)

    def _compute_query_vectors(self) -> dict[str, np.ndarray]:
        """预计算所有预定义查询的向量表示。"""
        if not self.predefined_cypher_dict:
            return {}

        query_keys, query_texts = build_query_texts(
            self.predefined_cypher_dict,
            self.query_descriptions,
        )

        # 计算向量表示
        vectors = self._embed_texts(query_texts)

        # 创建查询名到向量的映射
        return {key: np.array(vector) for key, vector in zip(query_keys, vectors)}

    def match_query(self, user_question: str, top_k: int = 3) -> list[dict[str, Any]]:
        """将用户问题匹配到最相似的预定义查询。"""
        if not self.query_vectors:
            return []

        # 对用户问题进行向量化
        question_vector = np.array(self._embed_texts([user_question])[0])

        # 计算用户问题与所有预定义查询的相似度
        similarities = []
        for query_name, query_vector in self.query_vectors.items():
            similarity = cosine_similarity([question_vector], [query_vector])[0][0]
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
        param_names = self._extract_parameter_names(cypher_template)

        # 使用LLM提取参数（如果提供）
        if llm is not None:
            return self._extract_parameters_with_llm(
                user_question,
                param_names,
                query_name,
                llm,
            )

        # 使用简单规则进行参数提取
        return self._extract_parameters_with_rules(user_question, param_names)

    def _extract_parameter_names(self, cypher_template: str) -> list[str]:
        """提取 Cypher 模板中的参数名。"""
        return extract_parameter_names(cypher_template)

    def _extract_parameters_with_rules(
        self,
        user_question: str,
        param_names: list[str],
    ) -> dict[str, str]:
        """使用规则从用户问题中提取参数。"""
        return extract_parameters_with_rules(user_question, param_names)

    def _extract_parameters_with_llm(
        self,
        user_question: str,
        param_names: list[str],
        query_name: str,
        llm: Any,
    ) -> dict[str, str]:
        """使用 LLM 从用户问题中提取参数。"""
        from langchain_core.prompts import ChatPromptTemplate

        # 创建提示模板
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

        # 调用LLM
        response = llm.invoke(prompt)

        # 解析响应
        return self._parse_json_response(getattr(response, "content", ""))

    def _parse_json_response(self, content: str) -> dict[str, Any]:
        """从模型返回内容中提取 JSON 对象。"""
        return parse_json_response(content)


def create_vector_query_matcher(
    predefined_cypher_dict: dict[str, str],
    query_descriptions: dict[str, str] | None = None,
) -> VectorQueryMatcher:
    """
    创建并返回 VectorQueryMatcher 实例。

    参数:
    predefined_cypher_dict: 预定义的 Cypher 查询字典
    query_descriptions: 可选的查询描述字典

    返回:
    VectorQueryMatcher 实例
    """
    # 如果没有提供描述，为每个查询生成默认描述。
    if query_descriptions is None:
        query_descriptions = build_default_query_descriptions(predefined_cypher_dict)

    return VectorQueryMatcher(predefined_cypher_dict, query_descriptions)
