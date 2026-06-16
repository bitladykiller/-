"""预定义 Cypher 查询的向量匹配工具。

职责：
1. 将查询名称与描述编码为向量
2. 用用户问题匹配最相近的预定义 Cypher
3. 在命中后提取模板参数
"""

import json
import re
from typing import Any

import numpy as np
import requests
from app.shared.core.config import settings
from app.shared.core.json_utils import extract_first_json_object
from sklearn.metrics.pairwise import cosine_similarity


class _VectorQueryMatcher:
    """基于词向量的预定义 Cypher 查询匹配器。"""

    def __init__(
        self,
        predefined_cypher_dict: dict[str, str],
        query_descriptions: dict[str, str],
        similarity_threshold: float = 0.6,
    ):
        """
        初始化查询匹配器。

        参数:
        predefined_cypher_dict: 预定义的 Cypher 查询字典
        query_descriptions: 每个查询的描述信息字典，用于增强匹配
        similarity_threshold: 相似度阈值，低于该阈值的匹配将被忽略
        """
        self.predefined_cypher_dict = predefined_cypher_dict
        self.similarity_threshold = similarity_threshold

        # 使用环境变量获取 Ollama 的基础 URL 和模型名称。
        self.ollama_embedding_model = settings.OLLAMA_EMBEDDING_MODEL
        self.ollama_api_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/embed"

        # 预计算查询向量
        if not self.predefined_cypher_dict:
            self.query_vectors = {}
        else:
            query_keys = list(self.predefined_cypher_dict)
            query_texts = [
                f"{query_name} {query_descriptions.get(query_name, '')}".strip()
                for query_name in query_keys
            ]
            vectors = self._embed_texts(query_texts)
            self.query_vectors = {
                key: np.array(vector)
                for key, vector in zip(query_keys, vectors)
            }

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """使用 Ollama embedding API 将文本转换为向量。"""
        try:
            response = requests.post(
                self.ollama_api_url,
                json={"model": self.ollama_embedding_model, "input": texts},
                timeout=10,
            )
            response.raise_for_status()
            embeddings = response.json().get("embeddings", [])
            if isinstance(embeddings, list) and embeddings:
                return embeddings
        except Exception:
            pass
        return [[0.0] * 1024 for _ in range(len(texts))]

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
        llm,
    ) -> dict[str, str]:
        """从用户问题中提取参数。"""
        # 检查查询是否存在
        if query_name not in self.predefined_cypher_dict:
            return {}

        # 获取查询模板
        cypher_template = self.predefined_cypher_dict[query_name]

        # 提取参数列表
        param_names = re.findall(r"\$(\w+)", cypher_template)
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
        try:
            payload = extract_first_json_object(str(response.text))
            if payload is None:
                return {}
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
