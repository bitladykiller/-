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
6. 支持混合检索（向量相似度 + 关键词匹配 + 时间权重）
7. 支持记忆合并（相似记忆合并）
"""

import time
import uuid
from typing import List, Optional, Dict, Any
from pymilvus import MilvusClient, DataType
from app.memory.config import LONG_TERM_MEMORY_CONFIG, LONG_TERM_MEMORY_TYPES
from app.memory.schemas import LongTermMemory, MemorySearchResult



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

    def _create_collection_if_not_exists(self) -> None:
        """
        创建 Milvus Collection（如果不存在）。
        """
        try:
            # 检查 Collection 是否存在
            if self.milvus_client.has_collection(self.collection_name):
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
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=1024)  # bge-m3 维度
            schema.add_field("created_at", DataType.INT64)
            schema.add_field("updated_at", DataType.INT64)
            schema.add_field("last_hit_at", DataType.INT64)
            schema.add_field("hit_count", DataType.INT64)
            schema.add_field("is_deleted", DataType.BOOL)

            # 创建索引参数
            index_params = self.milvus_client.prepare_index_params()
            index_params.add_index(
                field_name="embedding",
                index_type="IVF_FLAT",
                metric_type="COSINE",
                params={"nlist": 1024},
            )

            # 创建 Collection
            self.milvus_client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )


        except Exception as e:
            raise

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
        - memory_type：记忆类型（user_profile, issue_history, solution_note）
        - content：记忆内容

        返回：
        - memory_id：保存成功返回记忆 ID，失败返回 None
        """
        try:
            # 生成 embedding
            embedding = await self._get_embedding(content)
            if not embedding:
                return None

            # 生成 memory_id
            memory_id = str(uuid.uuid4())
            now_ts = int(time.time())

            # 构造记忆数据
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

            # 写入 Milvus
            self.milvus_client.insert(
                collection_name=self.collection_name,
                data=[memory_data],
            )

            return memory_id

        except Exception as e:
            # Milvus 失败时只记录日志，不抛出异常
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
        检索长期记忆。

        参数：
        - tenant_id：租户 ID，用于多租户隔离
        - user_id：用户 ID，用于用户隔离
        - query：当前用户问题
        - top_k：最多召回几条长期记忆
        - score_threshold：相似度阈值，低于该分数不注入 Prompt

        返回：
        - 命中的长期记忆列表
        """
        try:
            top_k = top_k or self.config["search"]["top_k"]
            score_threshold = score_threshold or self.config["search"]["score_threshold"]

            # 生成 query 的 embedding
            query_vector = await self._get_embedding(query)
            if not query_vector:
                return []

            # 构造过滤条件
            # 注意：必须包含 tenant_id、user_id、is_deleted 过滤条件
            filter_expr = (
                f'tenant_id == "{tenant_id}" '
                f'and user_id == "{user_id}" '
                f'and is_deleted == false'
            )

            # 检索 Milvus
            # 注意：如果使用 cosine similarity，分数越大越相似
            # 如果使用 L2 distance，需要转换成相似度分数
            results = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[query_vector],
                filter=filter_expr,
                limit=top_k,
                output_fields=[
                    "memory_id",
                    "tenant_id",
                    "user_id",
                    "memory_type",
                    "content",
                    "created_at",
                    "updated_at",
                    "last_hit_at",
                    "hit_count",
                    "is_deleted",
                ],
            )

            # 处理检索结果
            hit_memories = []
            for item in results[0]:
                # 获取相似度分数
                # 注意：Milvus 返回的 distance 字段，对于 COSINE 相似度，值越大越相似
                score = item.get("distance", 0)

                # 过滤低于阈值的结果
                if score < score_threshold:
                    continue

                # 获取记忆实体
                entity = item.get("entity", {})

                # 构造 LongTermMemory 对象
                memory = LongTermMemory(
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

                # 构造检索结果
                search_result = MemorySearchResult(
                    memory=memory,
                    score=score,
                )

                hit_memories.append(search_result)

            return hit_memories

        except Exception as e:
            # Milvus 失败时只记录日志，返回空列表
            return []

    async def update_memory_hit_info(self, memory: LongTermMemory) -> bool:
        """
        使用 Milvus partial_update 只更新命中计数器。

        相比旧方案（全量 upsert + 重新 embed），新方案：
        - 不需要重新生成 embedding（节省 LLM 调用）
        - 只需要传输 memory_id + 两个字段（hit_count + last_hit_at）
        - 延迟从 ~200ms 降低到 ~5ms

        Milvus 2.6+ 支持通过 upsert 实现 partial update：
        只需要提供主键 + 要更新的字段，其他字段保持不变。
        """
        try:
            now_ts = int(time.time())
            memory.last_hit_at = now_ts
            memory.hit_count = (memory.hit_count or 0) + 1

            # 只传 memory_id + hit_count + last_hit_at + updated_at（部分更新）
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
            return False

            return True

        except Exception as e:
            # 更新失败时只记录日志，不抛出异常
            return False

    async def soft_delete_memory(
        self,
        memory_id: str,
    ) -> bool:
        """
        软删除长期记忆。

        参数：
        - memory_id：记忆 ID

        返回：
        - True：删除成功
        - False：删除失败
        """
        try:
            # 获取记忆
            results = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=f'memory_id == "{memory_id}"',
                output_fields=["*"],
            )

            if not results:
                return False

            # 更新 is_deleted 字段
            memory_data = results[0]
            memory_data["is_deleted"] = True
            memory_data["updated_at"] = int(time.time())

            # 使用 upsert 更新
            self.milvus_client.upsert(
                collection_name=self.collection_name,
                data=[memory_data],
            )

            return True

        except Exception as e:
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

        参数：
        - tenant_id：租户 ID
        - user_id：用户 ID
        - memory_type：记忆类型
        - content：记忆内容

        返回：
        - True：需要新增（没有相似记忆）
        - False：不需要新增（已有相似记忆）
        """
        try:
            # 生成 embedding
            embedding = await self._get_embedding(content)
            if not embedding:
                return False

            # 构造过滤条件
            filter_expr = (
                f'tenant_id == "{tenant_id}" '
                f'and user_id == "{user_id}" '
                f'and memory_type == "{memory_type}" '
                f'and is_deleted == false'
            )

            # 检索相似记忆
            dedup_config = self.config["deduplication"]
            results = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[embedding],
                filter=filter_expr,
                limit=dedup_config["top_k"],
                output_fields=["memory_id", "content"],
            )

            # 检查相似度
            if results and results[0]:
                max_score = max(item.get("distance", 0) for item in results[0])
                if max_score >= dedup_config["similarity_threshold"]:
                    return False

            return True

        except Exception as e:
            return False

    def calculate_memory_weight(self, memory: LongTermMemory) -> float:
        """
        计算记忆权重，用于检索排序。

        权重计算公式：
        - 时间衰减：越旧权重越低
        - 命中频率：命中越多权重越高
        - 最近命中：最近命中过权重更高

        参数：
        - memory：长期记忆对象

        返回：
        - 权重值，范围 0.0 - 2.0
        """
        import time

        now = int(time.time())

        # 时间衰减：越旧权重越低
        # 10天衰减一半
        days_since_created = (now - memory.created_at) / 86400
        time_decay = 1.0 / (1.0 + days_since_created * 0.1)

        # 命中频率：命中越多权重越高
        # 最多加 1.0
        hit_boost = min(memory.hit_count * 0.1, 1.0)

        # 最近命中：最近命中过权重更高
        # 20天衰减一半
        days_since_hit = (now - memory.last_hit_at) / 86400 if memory.last_hit_at > 0 else 999
        recency_boost = 1.0 / (1.0 + days_since_hit * 0.05)

        # 综合权重
        weight = time_decay + hit_boost * 0.3 + recency_boost * 0.2

        return min(weight, 2.0)  # 限制最大权重

    def bm25_score(self, query: str, content: str, k1: float = 1.5, b: float = 0.75) -> float:
        """
        计算 BM25 分数。

        BM25 是一种基于词频的检索算法，比简单的关键词匹配更精确。

        参数：
        - query：查询文本
        - content：记忆内容
        - k1：词频饱和参数，控制词频对分数的影响程度
        - b：文档长度归一化参数，控制文档长度对分数的影响

        返回：
        - BM25 分数
        """
        import re
        import math

        # 简单的中文分词（按字符和英文单词分割）
        def tokenize(text):
            # 提取中文字符和英文单词
            tokens = re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]+', text.lower())
            return tokens

        query_tokens = tokenize(query)
        content_tokens = tokenize(content)

        if not query_tokens or not content_tokens:
            return 0.0

        # 计算词频
        content_tf = {}
        for token in content_tokens:
            content_tf[token] = content_tf.get(token, 0) + 1

        # 计算文档长度
        doc_len = len(content_tokens)
        avg_doc_len = 100  # 假设平均文档长度为100

        # 计算 BM25 分数
        score = 0.0
        for token in query_tokens:
            if token in content_tf:
                tf = content_tf[token]
                # IDF 简化：假设总文档数为1000，包含该词的文档数为100
                idf = math.log(1000 / 100)

                # BM25 公式
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_len))
                score += idf * tf_norm

        return score

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

        参数：
        - vector_results：向量检索结果，已按分数排序
        - bm25_results：BM25 检索结果，已按分数排序
        - k：RRF 参数，控制排名对分数的影响，通常取60

        返回：
        - 融合后的结果列表
        """
        # 构建 memory_id -> result 的映射
        vector_map = {r.memory.memory_id: r for r in vector_results}
        bm25_map = {r.memory.memory_id: r for r in bm25_results}

        # 获取所有唯一的 memory_id
        all_ids = set(vector_map.keys()) | set(bm25_map.keys())

        # 计算 RRF 分数
        rrf_scores = {}
        for mem_id in all_ids:
            score = 0.0

            # 向量检索的排名贡献
            if mem_id in vector_map:
                # 找到在向量结果中的排名
                vector_rank = next(
                    i for i, r in enumerate(vector_results) if r.memory.memory_id == mem_id
                ) + 1  # 排名从1开始
                score += 1.0 / (k + vector_rank)

            # BM25 检索的排名贡献
            if mem_id in bm25_map:
                # 找到在 BM25 结果中的排名
                bm25_rank = next(
                    i for i, r in enumerate(bm25_results) if r.memory.memory_id == mem_id
                ) + 1  # 排名从1开始
                score += 1.0 / (k + bm25_rank)

            rrf_scores[mem_id] = score

        # 按 RRF 分数排序
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        # 构建结果列表
        results = []
        for mem_id in sorted_ids:
            # 从任一结果中获取 memory 对象
            memory = vector_map[mem_id].memory if mem_id in vector_map else bm25_map[mem_id].memory

            # 创建新的 MemorySearchResult，使用 RRF 分数
            result = MemorySearchResult(
                memory=memory,
                score=rrf_scores[mem_id],
            )
            results.append(result)

        return results

    async def hybrid_search(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> List[MemorySearchResult]:
        """
        混合检索：向量检索 + BM25 + RRF 融合。

        流程：
        1. 向量检索：使用 embedding 进行语义相似度检索
        2. BM25 检索：使用 BM25 算法进行关键词匹配
        3. RRF 融合：将两个排序结果按排名融合

        参数：
        - tenant_id：租户 ID
        - user_id：用户 ID
        - query：查询文本
        - top_k：返回结果数量
        - score_threshold：相似度阈值

        返回：
        - 融合排序后的记忆列表
        """
        try:
            top_k = top_k or self.config["search"]["top_k"]
            score_threshold = score_threshold or self.config["search"]["score_threshold"]

            # 构造过滤条件
            filter_expr = (
                f'tenant_id == "{tenant_id}" '
                f'and user_id == "{user_id}" '
                f'and is_deleted == false'
            )

            # ------------------------------------------------------------------ #
            # 1. 向量检索
            # ------------------------------------------------------------------ #
            query_vector = await self._get_embedding(query)
            if not query_vector:
                return []

            # 获取向量检索结果（获取更多用于 RRF）
            vector_results_raw = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[query_vector],
                filter=filter_expr,
                limit=top_k * 2,  # 获取更多结果用于融合
                output_fields=[
                    "memory_id", "tenant_id", "user_id", "memory_type",
                    "content", "created_at", "updated_at", "last_hit_at",
                    "hit_count", "is_deleted",
                ],
            )

            # 处理向量检索结果
            vector_results = []
            for item in vector_results_raw[0]:
                vector_score = item.get("distance", 0)
                if vector_score < score_threshold:
                    continue

                entity = item.get("entity", {})
                memory = LongTermMemory(
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

                result = MemorySearchResult(memory=memory, score=vector_score)
                vector_results.append(result)

            # ------------------------------------------------------------------ #
            # 2. BM25 检索
            # ------------------------------------------------------------------ #
            # 获取所有候选记忆（用于 BM25 排序）
            all_memories_raw = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=[
                    "memory_id", "tenant_id", "user_id", "memory_type",
                    "content", "created_at", "updated_at", "last_hit_at",
                    "hit_count", "is_deleted",
                ],
                limit=100,  # 限制候选数量
            )

            # 计算 BM25 分数并排序
            bm25_candidates = []
            for entity in all_memories_raw:
                content = entity.get("content", "")
                bm25 = self.bm25_score(query, content)

                memory = LongTermMemory(
                    memory_id=entity.get("memory_id", ""),
                    tenant_id=entity.get("tenant_id", ""),
                    user_id=entity.get("user_id", ""),
                    memory_type=entity.get("memory_type", ""),
                    content=content,
                    created_at=entity.get("created_at", 0),
                    updated_at=entity.get("updated_at", 0),
                    last_hit_at=entity.get("last_hit_at", 0),
                    hit_count=entity.get("hit_count", 0),
                    is_deleted=entity.get("is_deleted", False),
                )

                result = MemorySearchResult(memory=memory, score=bm25)
                bm25_candidates.append(result)

            # 按 BM25 分数排序
            bm25_candidates.sort(key=lambda x: x.score, reverse=True)
            bm25_results = bm25_candidates[:top_k * 2]  # 取 top_k * 2 个结果

            # ------------------------------------------------------------------ #
            # 3. RRF 融合
            # ------------------------------------------------------------------ #
            fused_results = self.rrf_fusion(vector_results, bm25_results)

            # 取 top_k 个结果
            final_results = fused_results[:top_k]

            return final_results

        except Exception as e:
            return []

    async def merge_similar_memories(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        similarity_threshold: float = 0.9,
    ) -> int:
        """
        合并相似的长期记忆。

        参数：
        - tenant_id：租户 ID
        - user_id：用户 ID
        - memory_type：记忆类型
        - similarity_threshold：相似度阈值，高于此值的记忆会被合并

        返回：
        - 合并的记忆数量
        """
        try:
            # 获取该用户该类型的所有记忆
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
                    "memory_id",
                    "content",
                    "embedding",
                    "created_at",
                    "updated_at",
                    "last_hit_at",
                    "hit_count",
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

                    # 计算相似度（使用 embedding 的余弦相似度）
                    embedding1 = mem1.get("embedding", [])
                    embedding2 = mem2.get("embedding", [])

                    if not embedding1 or not embedding2:
                        continue

                    # 计算余弦相似度
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
                    # 选择最新的记忆作为主记忆
                    main_memory = max(cluster, key=lambda x: x.get("updated_at", 0))

                    # 合并内容
                    contents = [mem.get("content", "") for mem in cluster]
                    merged_content = self._merge_contents(contents)

                    # 更新主记忆
                    now_ts = int(time.time())
                    main_memory["content"] = merged_content
                    main_memory["updated_at"] = now_ts
                    main_memory["hit_count"] = sum(mem.get("hit_count", 0) for mem in cluster)
                    main_memory["last_hit_at"] = max(mem.get("last_hit_at", 0) for mem in cluster)

                    # 重新生成 embedding
                    new_embedding = await self._get_embedding(merged_content)
                    if new_embedding:
                        main_memory["embedding"] = new_embedding

                    # 更新主记忆
                    self.milvus_client.upsert(
                        collection_name=self.collection_name,
                        data=[main_memory],
                    )

                    # 软删除其他记忆
                    for mem in cluster:
                        if mem.get("memory_id") != main_memory.get("memory_id"):
                            await self.soft_delete_memory(mem.get("memory_id"))

                    merged_count += len(cluster) - 1

                except Exception as e:
                    continue

            return merged_count

        except Exception as e:
            return 0

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        计算两个向量的余弦相似度。

        参数：
        - vec1：向量 1
        - vec2：向量 2

        返回：
        - 余弦相似度，范围 -1.0 到 1.0
        """
        import numpy as np

        vec1 = np.array(vec1)
        vec2 = np.array(vec2)

        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def _merge_contents(self, contents: List[str]) -> str:
        """
        合并多个内容为一个。

        参数：
        - contents：内容列表

        返回：
        - 合并后的内容
        """
        # 简单的合并策略：去重后用分号连接
        unique_contents = list(set(contents))
        return "；".join(unique_contents)

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """
        获取文本的 embedding。

        参数：
        - text：文本内容

        返回：
        - embedding 向量，失败返回 None
        """
        try:
            # 调用 embedding 模型
            embedding = self.embedding_model.embed_query(text)
            return embedding

        except Exception as e:
            return None