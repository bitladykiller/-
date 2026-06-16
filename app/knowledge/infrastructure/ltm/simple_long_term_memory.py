"""Milvus 长期记忆存储层。

这个模块负责：
- 管理长期记忆的 Milvus collection 与索引
- 提供混合检索、去重和软删除能力

这个模块不负责：
- 对话级编排
- LLM 抽取逻辑
- Prompt 构造
"""

import time
import uuid
from typing import Any

from app.knowledge.domain.schemas import LongTermMemory
from app.knowledge.infrastructure.config import (
    LONG_TERM_MEMORY_CONFIG,
)
from app.shared.core.logger import get_logger
from pymilvus import DataType, Function, FunctionType, MilvusClient
from shared_retrieval.milvus_hybrid_core import MilvusHybridSearchCore

logger = get_logger(__name__)


class SimpleLongTermMemory:
    """
    简化版长期记忆模块。

    LTM = Long-Term Memory，长期记忆。
    作用：
    1. 向 Milvus 写入用户长期记忆。
    2. 根据用户当前问题检索长期记忆。
    3. 根据当前问题检索已保存的长期记忆。
    """

    def __init__(
        self,
        milvus_client: MilvusClient,
        embedding_model,
    ):
        """
        初始化长期记忆模块。

        参数：
        - milvus_client：Milvus 客户端
        - embedding_model：Embedding 模型，需要有 embed_query 方法
        """
        self.milvus_client = milvus_client
        self.embedding_model = embedding_model
        self.search_config = LONG_TERM_MEMORY_CONFIG["search"]
        self.deduplication_config = LONG_TERM_MEMORY_CONFIG["deduplication"]
        self.collection_name = LONG_TERM_MEMORY_CONFIG["collection_name"]

        # 初始化 Collection
        try:
            created = self._ensure_memory_collection(
                self.milvus_client,
                self.collection_name,
            )
            if not created:
                logger.info(f"Collection {self.collection_name} 已存在")
            else:
                logger.info(
                    f"Collection {self.collection_name} 创建成功（含 BM25 全文索引）"
                )
        except Exception as exc:
            logger.error(
                f"创建 Collection {self.collection_name} 失败 | {exc}",
                exc_info=True,
            )
            raise
        self.retrieval_core = MilvusHybridSearchCore(
            milvus_client=self.milvus_client,
            embedding_model=self.embedding_model,
            collection_name=self.collection_name,
            dense_field="embedding",
            sparse_field="sparse_vector",
            dense_metric_type="COSINE",
            dense_search_params={"nprobe": 16},
            hybrid_rrf_k=60,
        )

    @staticmethod
    def _ensure_memory_collection(
        milvus_client: MilvusClient,
        collection_name: str,
    ) -> bool:
        """确保长期记忆 collection 存在。"""
        if milvus_client.has_collection(collection_name):
            return False

        schema = milvus_client.create_schema(
            auto_id=False,
            enable_dynamic_field=True,
        )
        schema.add_field("memory_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
        schema.add_field("user_id", DataType.VARCHAR, max_length=64)
        schema.add_field("memory_type", DataType.VARCHAR, max_length=32)
        schema.add_field("content", DataType.VARCHAR, max_length=4096)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=1024)
        schema.add_field("created_at", DataType.INT64)
        schema.add_field("updated_at", DataType.INT64)
        schema.add_field("last_hit_at", DataType.INT64)
        schema.add_field("hit_count", DataType.INT64)
        schema.add_field("is_deleted", DataType.BOOL)
        schema.add_function(
            Function(
                name="bm25",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["sparse_vector"],
            )
        )
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

        index_params = milvus_client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 1024},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )

        milvus_client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )
        return True

    # ------------------------------------------------------------------ #
    # Collection 管理
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 记忆 CRUD
    # ------------------------------------------------------------------ #

    async def save_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
    ) -> None:
        """
        保存长期记忆。

        参数：
        - tenant_id：租户 ID
        - user_id：用户 ID
        - memory_type：记忆类型
        - content：记忆内容

        """
        try:
            embedding = await self._get_embedding(content)
            if not embedding:
                logger.warning(
                    f"保存记忆失败：embedding 生成返回空 | tenant={tenant_id} "
                    f"user={user_id} type={memory_type}"
                )
                return

            now_ts = int(time.time())
            memory_id = str(uuid.uuid4())
            memory_data: dict[str, Any] = {
                "memory_id": memory_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "memory_type": memory_type,
                "content": content,
                "embedding": embedding,
                "created_at": now_ts,
                "updated_at": now_ts,
                "last_hit_at": 0,
                "hit_count": 0,
                "is_deleted": False,
            }
            self.milvus_client.insert(
                collection_name=self.collection_name,
                data=[memory_data],
            )
        except Exception as exc:
            logger.error(
                f"保存记忆异常 | tenant={tenant_id} user={user_id} "
                f"type={memory_type} | {exc}",
                exc_info=True,
            )
            return

    async def deduplicate_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
    ) -> bool:
        """
        去重检查，判断是否需要新增长期记忆。

        返回：
        - True：需要新增（没有相似记忆）
        - False：不需要新增（已有相似记忆）
        """
        try:
            embedding = await self._get_embedding(content)
            if not embedding:
                return False

            filter_expr = " and ".join(
                [
                    f'tenant_id == "{tenant_id}"',
                    f'user_id == "{user_id}"',
                    f'memory_type == "{memory_type}"',
                    'is_deleted == false',
                ]
            )
            results = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[list(embedding)],
                filter=filter_expr,
                limit=self.deduplication_config["top_k"],
            )
            if not results or not results[0]:
                return True

            max_score = max(item.get("distance", 0) for item in results[0])
            return max_score < self.deduplication_config["similarity_threshold"]
        except Exception as exc:
            logger.error(
                f"deduplicate_memory 异常 | tenant={tenant_id} "
                f"user={user_id} type={memory_type} | {exc}",
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------ #
    # 混合检索（核心变更：Milvus 内置 BM25 替换手动 BM25）
    # ------------------------------------------------------------------ #

    async def hybrid_search(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[LongTermMemory]:
        """
        混合检索：向量检索 + Milvus BM25 + RRF 融合。

        流程：
        1. 向量检索：使用 embedding 进行语义相似度检索
        2. Milvus BM25 检索：使用 Milvus 内置 BM25 全文检索
        3. RRF 融合：将两个排序结果按排名融合

        WHY：
        这里优先使用 Milvus 内置 BM25，而不是在客户端手动做关键词打分：
        - BM25 在 Milvus 服务端计算，IDF 统计来自实际集合
        - 不需要客户端拉取全部记忆做关键词打分
        - 检索更准确、延迟更低
        """
        try:
            resolved_top_k = (
                top_k if top_k is not None else self.search_config["top_k"]
            )
            resolved_score_threshold = (
                score_threshold
                if score_threshold is not None
                else self.search_config["score_threshold"]
            )
            filter_expr = " and ".join(
                [
                    f'tenant_id == "{tenant_id}"',
                    f'user_id == "{user_id}"',
                    'is_deleted == false',
                ]
            )
            hits = await self.retrieval_core.search_hybrid(
                query,
                limit=resolved_top_k,
                filter_expr=filter_expr,
                output_fields=[
                    "memory_type",
                    "content",
                ],
                score_threshold=resolved_score_threshold,
                search_limit=resolved_top_k * 2,
            )
            search_results: list[LongTermMemory] = []
            for hit in hits:
                entity = hit["entity"]
                search_results.append(LongTermMemory(**entity))
            return search_results
        except Exception as exc:
            logger.error(
                f"hybrid_search 异常 | tenant={tenant_id} user={user_id} "
                f"query={query[:100]} | {exc}",
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------ #
    # 内部工具方法
    # ------------------------------------------------------------------ #

    async def _get_embedding(self, text: str) -> list[float] | None:
        """获取文本的 embedding 向量。"""
        try:
            embedding = self.embedding_model.embed_query(text)
            return embedding

        except Exception as exc:
            logger.error(
                f"embedding 生成异常 | "
                f"text_preview={text[:200]} | {exc}",
                exc_info=True,
            )
            return None
