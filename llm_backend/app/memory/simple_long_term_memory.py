"""Milvus 长期记忆存储层。

这个模块负责：
- 管理长期记忆的 Milvus collection 与索引
- 提供向量检索、混合检索、命中刷新和软删除能力
- 处理相似记忆聚类与合并

这个模块不负责：
- 对话级编排
- LLM 抽取逻辑
- Prompt 构造
"""
from __future__ import annotations

import time

from pymilvus import MilvusClient

from app.memory.config import (
    long_term_collection_name,
    long_term_deduplication_config,
    long_term_search_config,
    long_term_update_on_hit_config,
)
from app.memory.schemas import LongTermMemory, MemorySearchResult
from app.core.logger import get_logger
from shared_retrieval import MilvusHybridSearchCore
from app.memory.ltm_collection import (
    DEDUP_OUTPUT_FIELDS,
    MEMORY_OUTPUT_FIELDS,
    MERGE_QUERY_FIELDS,
    insert_records,
    query_records,
    upsert_records,
)
from app.memory.ltm_operation_utils import (
    build_cluster_merge_plan,
    build_hit_update_plan,
    build_new_memory_insert_record,
    build_soft_delete_record,
    preview_text,
    resolve_active_search_request,
)
from app.memory.ltm_runtime_support import (
    create_default_retrieval_core,
    ensure_collection_ready,
    load_merge_clusters,
    search_dense_memories,
    search_hybrid_memories,
    should_insert_memory,
)
from app.memory.ltm_store_utils import (
    MilvusRecord,
    build_active_memory_filter,
    build_memory_id_filter,
)

logger = get_logger(__name__)
SEARCH_LOG_PREVIEW_LIMIT = 100
EMBEDDING_LOG_PREVIEW_LIMIT = 200
HYBRID_SEARCH_LIMIT_MULTIPLIER = 2


class SimpleLongTermMemory:
    """
    简化版长期记忆模块。

    LTM = Long-Term Memory，长期记忆。
    作用：
    1. 向 Milvus 写入用户长期记忆。
    2. 根据用户当前问题检索长期记忆。
    3. 命中长期记忆后刷新 last_hit_at 和 hit_count。
    """

    def __init__(
        self,
        milvus_client: MilvusClient,
        embedding_model,
        collection_name: str | None = None,
        retrieval_core: MilvusHybridSearchCore | None = None,
    ):
        """
        初始化长期记忆模块。

        参数：
        - milvus_client：Milvus 客户端
        - embedding_model：Embedding 模型，需要有 embed_query 方法
        - collection_name：Collection 名称，默认从配置读取
        - retrieval_core：可选的检索核心注入点，便于单测或替换底层检索实现
        """
        self.milvus_client = milvus_client
        self.embedding_model = embedding_model
        self.search_config = long_term_search_config()
        self.deduplication_config = long_term_deduplication_config()
        self.update_on_hit_config = long_term_update_on_hit_config()
        self.collection_name = collection_name or long_term_collection_name()

        # 初始化 Collection
        self._ensure_collection_ready()
        self.retrieval_core = retrieval_core or create_default_retrieval_core(
            milvus_client=self.milvus_client,
            embedding_model=self.embedding_model,
            collection_name=self.collection_name,
        )

    # ------------------------------------------------------------------ #
    # Collection 管理
    # ------------------------------------------------------------------ #

    @staticmethod
    def _now_ts() -> int:
        """统一生成秒级时间戳。"""
        return int(time.time())

    def _ensure_collection_ready(self) -> None:
        """
        创建 Milvus Collection（如果不存在）。

        包含两个索引：
        - 稠密向量索引（embedding）：用于语义相似度检索
        - 稀疏向量索引（sparse_vector）：Milvus 内置 BM25 全文检索
        """
        try:
            ensure_collection_ready(
                milvus_client=self.milvus_client,
                collection_name=self.collection_name,
                logger=logger,
            )
        except Exception as exc:
            logger.error(
                f"创建 Collection {self.collection_name} 失败 | {exc}",
                exc_info=True,
            )
            raise

    async def _merge_memory_cluster(self, cluster: list[MilvusRecord]) -> int:
        """合并一个相似记忆簇，返回本次被折叠的记录数。"""
        now_ts = self._now_ts()
        merge_plan = build_cluster_merge_plan(cluster, now_ts)
        merged_record = merge_plan["merged_record"]
        merged_content = merge_plan["merged_content"]

        new_embedding = await self._get_embedding(merged_content)
        if new_embedding:
            merged_record["embedding"] = new_embedding

        upsert_records(self.milvus_client, self.collection_name, [merged_record])

        for memory_id in merge_plan["deleted_memory_ids"]:
            await self.soft_delete_memory(memory_id)

        return len(cluster) - 1

    def _memory_exists(self, memory_id: str) -> bool:
        """判断某条长期记忆是否仍存在于集合中。"""
        results = query_records(
            self.milvus_client,
            self.collection_name,
            build_memory_id_filter(memory_id),
            ["memory_id"],
        )
        return bool(results)

    def _resolve_active_search(
        self,
        tenant_id: str,
        user_id: str,
        top_k: int | None,
        score_threshold: float | None,
    ) -> tuple[str, int, float]:
        """统一解析“活跃长期记忆”的过滤条件与检索参数。"""
        return resolve_active_search_request(
            self.search_config,
            tenant_id,
            user_id,
            top_k,
            score_threshold,
        )

    # ------------------------------------------------------------------ #
    # 记忆 CRUD
    # ------------------------------------------------------------------ #

    async def save_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
    ) -> str | None:
        """
        保存长期记忆。

        参数：
        - tenant_id：租户 ID
        - user_id：用户 ID
        - memory_type：记忆类型
        - content：记忆内容

        返回：
        - memory_id：保存成功返回记忆 ID，失败返回 None
        """
        try:
            embedding = await self._get_embedding(content)
            if not embedding:
                logger.warning(
                    f"保存记忆失败：embedding 生成返回空 | tenant={tenant_id} "
                    f"user={user_id} type={memory_type}"
                )
                return None

            now_ts = self._now_ts()
            memory_id, memory_data = build_new_memory_insert_record(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_type=memory_type,
                content=content,
                embedding=embedding,
                now_ts=now_ts,
            )

            insert_records(
                self.milvus_client,
                self.collection_name,
                [memory_data],
            )

            return memory_id

        except Exception as exc:
            logger.error(
                f"保存记忆异常 | tenant={tenant_id} user={user_id} "
                f"type={memory_type} | {exc}",
                exc_info=True,
            )
            return None

    async def search_memory(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[MemorySearchResult]:
        """
        检索长期记忆（仅向量检索，不含 BM25）。

        参数：
        - tenant_id：租户 ID
        - user_id：用户 ID
        - query：当前用户问题
        - top_k：最多召回几条
        - score_threshold：相似度阈值

        返回：
        - 命中的长期记忆列表
        """
        try:
            filter_expr, top_k, score_threshold = self._resolve_active_search(
                tenant_id,
                user_id,
                top_k,
                score_threshold,
            )
            return await search_dense_memories(
                retrieval_core=self.retrieval_core,
                query=query,
                filter_expr=filter_expr,
                output_fields=MEMORY_OUTPUT_FIELDS,
                score_threshold=score_threshold,
                top_k=top_k,
            )

        except Exception as exc:
            logger.error(
                f"search_memory 异常 | tenant={tenant_id} user={user_id} "
                f"query={preview_text(query, SEARCH_LOG_PREVIEW_LIMIT)} | {exc}",
                exc_info=True,
            )
            return []

    async def update_memory_hit_info(self, memory: LongTermMemory) -> bool:
        """
        使用 Milvus partial_update 更新命中计数器。

        只传 memory_id + hit_count + last_hit_at + updated_at（部分更新），
        不需要重新生成 embedding，也不需要传输全量字段。
        """
        try:
            if not self.update_on_hit_config["enabled"]:
                return True

            now_ts = self._now_ts()
            update_plan = build_hit_update_plan(
                memory,
                self.update_on_hit_config,
                now_ts,
            )
            memory.hit_count = update_plan["hit_count"]
            memory.last_hit_at = update_plan["last_hit_at"]
            upsert_records(
                self.milvus_client,
                self.collection_name,
                [update_plan["update_record"]],
            )
            return True

        except Exception as exc:
            logger.error(
                f"update_memory_hit_info 异常 | memory_id={memory.memory_id} | {exc}",
                exc_info=True,
            )
            return False

    async def soft_delete_memory(self, memory_id: str) -> bool:
        """
        软删除长期记忆。
        """
        try:
            if not self._memory_exists(memory_id):
                return False

            memory_data = build_soft_delete_record(memory_id, self._now_ts())
            upsert_records(
                self.milvus_client,
                self.collection_name,
                [memory_data],
            )
            return True

        except Exception as exc:
            logger.error(
                f"soft_delete_memory 异常 | memory_id={memory_id} | {exc}",
                exc_info=True,
            )
            return False

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

            filter_expr = build_active_memory_filter(
                tenant_id,
                user_id,
                memory_type,
            )
            return should_insert_memory(
                milvus_client=self.milvus_client,
                collection_name=self.collection_name,
                embedding=embedding,
                filter_expr=filter_expr,
                top_k=self.deduplication_config["top_k"],
                output_fields=DEDUP_OUTPUT_FIELDS,
                similarity_threshold=self.deduplication_config["similarity_threshold"],
            )

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
    ) -> list[MemorySearchResult]:
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
            filter_expr, top_k, score_threshold = self._resolve_active_search(
                tenant_id,
                user_id,
                top_k,
                score_threshold,
            )
            return await search_hybrid_memories(
                retrieval_core=self.retrieval_core,
                query=query,
                filter_expr=filter_expr,
                output_fields=MEMORY_OUTPUT_FIELDS,
                score_threshold=score_threshold,
                search_limit_multiplier=HYBRID_SEARCH_LIMIT_MULTIPLIER,
                top_k=top_k,
            )

        except Exception as exc:
            logger.error(
                f"hybrid_search 异常 | tenant={tenant_id} user={user_id} "
                f"query={preview_text(query, SEARCH_LOG_PREVIEW_LIMIT)} | {exc}",
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------ #
    # 记忆合并
    # ------------------------------------------------------------------ #

    async def merge_similar_memories(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        similarity_threshold: float = 0.9,
    ) -> int:
        """合并相似的长期记忆。"""
        try:
            filter_expr = build_active_memory_filter(
                tenant_id,
                user_id,
                memory_type,
            )
            clusters = load_merge_clusters(
                milvus_client=self.milvus_client,
                collection_name=self.collection_name,
                filter_expr=filter_expr,
                output_fields=MERGE_QUERY_FIELDS,
                similarity_threshold=similarity_threshold,
            )
            if not clusters:
                return 0

            # 合并每个聚类
            merged_count = 0
            for cluster in clusters:
                try:
                    merged_count += await self._merge_memory_cluster(cluster)

                except Exception as exc:
                    logger.warning(
                        f"合并记忆聚类异常 | tenant={tenant_id} "
                        f"user={user_id} | {exc}",
                        exc_info=True,
                    )
                    continue

            return merged_count

        except Exception as exc:
            logger.error(
                f"merge_similar_memories 异常 | tenant={tenant_id} "
                f"user={user_id} type={memory_type} | {exc}",
                exc_info=True,
            )
            return 0

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
                f"text_preview={preview_text(text, EMBEDDING_LOG_PREVIEW_LIMIT)} | {exc}",
                exc_info=True,
            )
            return None
