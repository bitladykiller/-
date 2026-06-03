"""
Milvus 长期记忆模块。

LTM = Long-Term Memory，长期记忆。
使用 Milvus 存储用户长期偏好、历史问题和有效解决方案。

关键设计：
1. 使用 Milvus 向量数据库存储长期记忆
2. 通过 tenant_id 和 user_id 做隔离
3. 每次用户请求前，根据当前问题检索长期记忆
4. 命中长期记忆后，更新 last_hit_at 和 hit_count
5. 支持记忆衰减（时间衰减 + 命中频率衰减）
6. 支持混合检索（向量相似度 + Milvus BM25 全文检索）
7. 支持记忆合并（相似记忆合并）

v3.3: BM25 检索从手动实现迁移到 Milvus 内置 BM25 Function。
"""

import time
import uuid
from typing import List, Optional, Dict, Any

from pymilvus import (
    MilvusClient,
    DataType,
    Function,
    FunctionType,
)

from app.memory.config import LONG_TERM_MEMORY_CONFIG
from app.memory.schemas import LongTermMemory, MemorySearchResult
from app.core.logger import get_logger
from shared_retrieval import MilvusHybridSearchCore

logger = get_logger(__name__)


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
        collection_name: Optional[str] = None,
    ):
        """
        初始化长期记忆模块。

        参数：
        - milvus_client：Milvus 客户端
        - embedding_model：Embedding 模型，需要有 embed_query 方法
        - collection_name：Collection 名称，默认从配置读取
        """
        self.milvus_client = milvus_client
        self.embedding_model = embedding_model
        self.collection_name = collection_name or LONG_TERM_MEMORY_CONFIG["collection_name"]
        self.config = LONG_TERM_MEMORY_CONFIG

        # 初始化 Collection
        self._create_collection_if_not_exists()
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

    # ------------------------------------------------------------------ #
    # Collection 管理
    # ------------------------------------------------------------------ #

    def _create_collection_if_not_exists(self) -> None:
        """
        创建 Milvus Collection（如果不存在）。

        包含两个索引：
        - 稠密向量索引（embedding）：用于语义相似度检索
        - 稀疏向量索引（sparse_vector）：Milvus 内置 BM25 全文检索
        """
        try:
            if self.milvus_client.has_collection(self.collection_name):
                logger.info(f"Collection {self.collection_name} 已存在")
                return

            # 定义 Collection Schema
            schema = self.milvus_client.create_schema(
                auto_id=False,
                enable_dynamic_field=True,
            )

            # 添加字段
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

            # 添加 BM25 Function：自动将 content 映射到 sparse_vector
            bm25_fn = Function(
                name="bm25",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["sparse_vector"],
            )
            schema.add_function(bm25_fn)

            # 稀疏向量字段（由 BM25 Function 自动填充）
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

            # 创建索引参数
            index_params = self.milvus_client.prepare_index_params()

            # 稠密向量索引（语义检索）
            index_params.add_index(
                field_name="embedding",
                index_type="IVF_FLAT",
                metric_type="COSINE",
                params={"nlist": 1024},
            )

            # 稀疏向量索引（BM25 全文检索）
            index_params.add_index(
                field_name="sparse_vector",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
            )

            # 创建 Collection
            self.milvus_client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )

            logger.info(f"Collection {self.collection_name} 创建成功（含 BM25 全文索引）")

        except Exception as e:
            logger.error(
                f"创建 Collection {self.collection_name} 失败 | {e}",
                exc_info=True,
            )
            raise

    # ------------------------------------------------------------------ #
    # 记忆 CRUD
    # ------------------------------------------------------------------ #

    async def save_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
    ) -> Optional[str]:
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

            memory_id = str(uuid.uuid4())
            now_ts = int(time.time())

            memory_data = {
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

            return memory_id

        except Exception as e:
            logger.error(
                f"保存记忆异常 | tenant={tenant_id} user={user_id} "
                f"type={memory_type} | {e}",
                exc_info=True,
            )
            return None

    async def search_memory(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> List[MemorySearchResult]:
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
            top_k = top_k or self.config["search"]["top_k"]
            score_threshold = score_threshold or self.config["search"]["score_threshold"]

            filter_expr = (
                f'tenant_id == "{tenant_id}" '
                f'and user_id == "{user_id}" '
                f'and is_deleted == false'
            )

            hits = await self.retrieval_core.search_dense(
                query,
                limit=top_k,
                filter_expr=filter_expr,
                output_fields=[
                    "memory_id", "tenant_id", "user_id", "memory_type",
                    "content", "created_at", "updated_at", "last_hit_at",
                    "hit_count", "is_deleted",
                ],
                score_threshold=score_threshold,
            )

            hit_memories = []
            for hit in hits:
                memory = self._entity_to_memory(hit["entity"])
                search_result = MemorySearchResult(memory=memory, score=hit["score"])
                hit_memories.append(search_result)

            return hit_memories

        except Exception as e:
            logger.error(
                f"search_memory 异常 | tenant={tenant_id} user={user_id} "
                f"query={query[:100]} | {e}",
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
            now_ts = int(time.time())
            memory.last_hit_at = now_ts
            memory.hit_count = (memory.hit_count or 0) + 1

            memory_data = {
                "memory_id": memory.memory_id,
                "hit_count": memory.hit_count,
                "last_hit_at": memory.last_hit_at,
                "updated_at": now_ts,
            }

            self.milvus_client.upsert(
                collection_name=self.collection_name,
                data=[memory_data],
            )
            return True

        except Exception as e:
            logger.error(
                f"update_memory_hit_info 异常 | memory_id={memory.memory_id} | {e}",
                exc_info=True,
            )
            return False

    async def soft_delete_memory(self, memory_id: str) -> bool:
        """
        软删除长期记忆。
        """
        try:
            results = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=f'memory_id == "{memory_id}"',
                output_fields=["*"],
            )

            if not results:
                return False

            memory_data = results[0]
            memory_data["is_deleted"] = True
            memory_data["updated_at"] = int(time.time())

            self.milvus_client.upsert(
                collection_name=self.collection_name,
                data=[memory_data],
            )

            return True

        except Exception as e:
            logger.error(
                f"soft_delete_memory 异常 | memory_id={memory_id} | {e}",
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

            filter_expr = (
                f'tenant_id == "{tenant_id}" '
                f'and user_id == "{user_id}" '
                f'and memory_type == "{memory_type}" '
                f'and is_deleted == false'
            )

            dedup_config = self.config["deduplication"]
            results = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[embedding],
                filter=filter_expr,
                limit=dedup_config["top_k"],
                output_fields=["memory_id", "content"],
            )

            if results and results[0]:
                max_score = max(item.get("distance", 0) for item in results[0])
                if max_score >= dedup_config["similarity_threshold"]:
                    return False

            return True

        except Exception as e:
            logger.error(
                f"deduplicate_memory 异常 | tenant={tenant_id} "
                f"user={user_id} type={memory_type} | {e}",
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------ #
    # 记忆权重与排序
    # ------------------------------------------------------------------ #

    def calculate_memory_weight(self, memory: LongTermMemory) -> float:
        """
        计算记忆权重，用于检索排序。

        权重计算公式：
        - 时间衰减：越旧权重越低（10天衰减一半）
        - 命中频率：命中越多权重越高（最多加 1.0）
        - 最近命中：最近命中过权重更高（20天衰减一半）

        返回：
        - 权重值，范围 0.0 - 2.0
        """
        now = int(time.time())

        days_since_created = (now - memory.created_at) / 86400
        time_decay = 1.0 / (1.0 + days_since_created * 0.1)

        hit_boost = min(memory.hit_count * 0.1, 1.0)

        days_since_hit = (
            (now - memory.last_hit_at) / 86400 if memory.last_hit_at > 0 else 999
        )
        recency_boost = 1.0 / (1.0 + days_since_hit * 0.05)

        weight = time_decay + hit_boost * 0.3 + recency_boost * 0.2
        return min(weight, 2.0)

    # ------------------------------------------------------------------ #
    # RRF 融合
    # ------------------------------------------------------------------ #

    def rrf_fusion(
        self,
        vector_results: List[MemorySearchResult],
        bm25_results: List[MemorySearchResult],
        k: int = 60,
    ) -> List[MemorySearchResult]:
        """
        RRF (Reciprocal Rank Fusion) 融合。

        将向量检索和 BM25 检索的结果按排名融合。
        RRF 公式：score = sum(1 / (k + rank_i))
        """
        vector_map = {r.memory.memory_id: r for r in vector_results}
        bm25_map = {r.memory.memory_id: r for r in bm25_results}

        all_ids = set(vector_map.keys()) | set(bm25_map.keys())

        rrf_scores = {}
        for mem_id in all_ids:
            score = 0.0

            if mem_id in vector_map:
                vector_rank = next(
                    i for i, r in enumerate(vector_results)
                    if r.memory.memory_id == mem_id
                ) + 1
                score += 1.0 / (k + vector_rank)

            if mem_id in bm25_map:
                bm25_rank = next(
                    i for i, r in enumerate(bm25_results)
                    if r.memory.memory_id == mem_id
                ) + 1
                score += 1.0 / (k + bm25_rank)

            rrf_scores[mem_id] = score

        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        results = []
        for mem_id in sorted_ids:
            memory = (
                vector_map[mem_id].memory
                if mem_id in vector_map
                else bm25_map[mem_id].memory
            )
            result = MemorySearchResult(memory=memory, score=rrf_scores[mem_id])
            results.append(result)

        return results

    # ------------------------------------------------------------------ #
    # 混合检索（核心变更：Milvus 内置 BM25 替换手动 BM25）
    # ------------------------------------------------------------------ #

    async def hybrid_search(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> List[MemorySearchResult]:
        """
        混合检索：向量检索 + Milvus BM25 + RRF 融合。

        流程：
        1. 向量检索：使用 embedding 进行语义相似度检索
        2. Milvus BM25 检索：使用 Milvus 内置 BM25 全文检索
        3. RRF 融合：将两个排序结果按排名融合

        相比 v3.2 的手动 BM25（硬编码 IDF + 客户端全量计算）：
        - BM25 在 Milvus 服务端计算，IDF 统计来自实际集合
        - 不需要客户端拉取全部记忆做关键词打分
        - 检索更准确、延迟更低
        """
        try:
            filter_expr = (
                f'tenant_id == "{tenant_id}" '
                f'and user_id == "{user_id}" '
                f'and is_deleted == false'
            )
            top_k = top_k or self.config["search"]["top_k"]
            score_threshold = score_threshold or self.config["search"]["score_threshold"]
            hits = await self.retrieval_core.search_hybrid(
                query,
                limit=top_k,
                filter_expr=filter_expr,
                output_fields=[
                    "memory_id", "tenant_id", "user_id", "memory_type",
                    "content", "created_at", "updated_at", "last_hit_at",
                    "hit_count", "is_deleted",
                ],
                score_threshold=score_threshold,
                search_limit=top_k * 2,
            )
            return [
                MemorySearchResult(
                    memory=self._entity_to_memory(hit["entity"]),
                    score=hit["score"],
                )
                for hit in hits
            ]

        except Exception as e:
            logger.error(
                f"hybrid_search 异常 | tenant={tenant_id} user={user_id} "
                f"query={query[:100]} | {e}",
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
            filter_expr = (
                f'tenant_id == "{tenant_id}" '
                f'and user_id == "{user_id}" '
                f'and memory_type == "{memory_type}" '
                f'and is_deleted == false'
            )

            results = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=[
                    "memory_id", "content", "embedding",
                    "created_at", "updated_at", "last_hit_at", "hit_count",
                ],
            )

            if not results or len(results) < 2:
                return 0

            # 聚类相似记忆
            clusters = []
            used_indices = set()

            for i, mem1 in enumerate(results):
                if i in used_indices:
                    continue

                cluster = [mem1]
                used_indices.add(i)

                for j, mem2 in enumerate(results):
                    if j in used_indices:
                        continue

                    embedding1 = mem1.get("embedding", [])
                    embedding2 = mem2.get("embedding", [])

                    if not embedding1 or not embedding2:
                        continue

                    similarity = self._cosine_similarity(embedding1, embedding2)
                    if similarity >= similarity_threshold:
                        cluster.append(mem2)
                        used_indices.add(j)

                if len(cluster) > 1:
                    clusters.append(cluster)

            # 合并每个聚类
            merged_count = 0
            for cluster in clusters:
                try:
                    main_memory = max(cluster, key=lambda x: x.get("updated_at", 0))
                    contents = [mem.get("content", "") for mem in cluster]
                    merged_content = self._merge_contents(contents)

                    now_ts = int(time.time())
                    main_memory["content"] = merged_content
                    main_memory["updated_at"] = now_ts
                    main_memory["hit_count"] = sum(
                        mem.get("hit_count", 0) for mem in cluster
                    )
                    main_memory["last_hit_at"] = max(
                        mem.get("last_hit_at", 0) for mem in cluster
                    )

                    new_embedding = await self._get_embedding(merged_content)
                    if new_embedding:
                        main_memory["embedding"] = new_embedding

                    self.milvus_client.upsert(
                        collection_name=self.collection_name,
                        data=[main_memory],
                    )

                    for mem in cluster:
                        if mem.get("memory_id") != main_memory.get("memory_id"):
                            await self.soft_delete_memory(mem.get("memory_id"))

                    merged_count += len(cluster) - 1

                except Exception as e:
                    logger.warning(
                        f"合并记忆聚类异常 | tenant={tenant_id} "
                        f"user={user_id} | {e}",
                        exc_info=True,
                    )
                    continue

            return merged_count

        except Exception as e:
            logger.error(
                f"merge_similar_memories 异常 | tenant={tenant_id} "
                f"user={user_id} type={memory_type} | {e}",
                exc_info=True,
            )
            return 0

    # ------------------------------------------------------------------ #
    # 内部工具方法
    # ------------------------------------------------------------------ #

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算两个向量的余弦相似度。"""
        import numpy as np

        vec1_arr = np.array(vec1)
        vec2_arr = np.array(vec2)

        dot_product = np.dot(vec1_arr, vec2_arr)
        norm1 = np.linalg.norm(vec1_arr)
        norm2 = np.linalg.norm(vec2_arr)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    def _merge_contents(self, contents: List[str]) -> str:
        """合并多个内容为一个（去重后用分号连接）。"""
        unique_contents = list(set(contents))
        return "；".join(unique_contents)

    def _entity_to_memory(self, entity: Dict[str, Any]) -> LongTermMemory:
        """将 Milvus entity 字典转换为 LongTermMemory 对象。"""
        return LongTermMemory(
            memory_id=entity.get("memory_id", ""),
            tenant_id=entity.get("tenant_id", ""),
            user_id=entity.get("user_id", ""),
            memory_type=entity.get("memory_type", ""),
            content=entity.get("content", ""),
            created_at=entity.get("created_at", 0),
            updated_at=entity.get("updated_at", 0),
            last_hit_at=entity.get("last_hit_at", 0),
            hit_count=entity.get("hit_count", 0),
            is_deleted=entity.get("is_deleted", False),
        )

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """获取文本的 embedding 向量。"""
        try:
            embedding = self.embedding_model.embed_query(text)
            return embedding

        except Exception as e:
            logger.error(
                f"embedding 生成异常 | text_preview={text[:200]} | {e}",
                exc_info=True,
            )
            return None
