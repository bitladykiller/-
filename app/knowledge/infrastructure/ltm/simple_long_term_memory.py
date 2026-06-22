"""Milvus 长期记忆存储层。

这个模块负责：
- 管理长期记忆的 Milvus collection 与索引
- 提供混合检索、去重、命中刷新和软删除能力

这个模块不负责：
- 对话级编排
- LLM 抽取逻辑
- Prompt 构造
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeAlias

from typing_extensions import TypedDict

from pymilvus import MilvusClient

from app.knowledge.infrastructure.config import (
    long_term_collection_name,
    long_term_deduplication_config,
    long_term_search_config,
    long_term_update_on_hit_config,
)
from app.knowledge.domain.schemas import LongTermMemory, MemorySearchResult
from app.shared.core.logger import get_logger
from shared_retrieval import MilvusHybridSearchCore
from app.knowledge.infrastructure.ltm.ltm_collection import (
    DEDUP_OUTPUT_FIELDS,
    MEMORY_OUTPUT_FIELDS,
    MilvusRecord,
    ensure_memory_collection,
    insert_records,
    search_records,
    upsert_records,
)

logger = get_logger(__name__)
SEARCH_LOG_PREVIEW_LIMIT = 100
EMBEDDING_LOG_PREVIEW_LIMIT = 200
HYBRID_SEARCH_LIMIT_MULTIPLIER = 2
LoggerLike: TypeAlias = Any
_NowProvider: TypeAlias = Callable[[], int]
EmbeddingGetter: TypeAlias = Callable[[str], Awaitable[list[float] | None]]
_MilvusHit: TypeAlias = Mapping[str, Any]


def entity_to_memory(entity: Mapping[str, Any]) -> LongTermMemory:
    """将 Milvus entity 字典转换为 LongTermMemory 对象。"""
    payload: dict[str, Any] = {
        "memory_id": "",
        "tenant_id": "",
        "user_id": "",
        "memory_type": "",
        "content": "",
        "created_at": 0,
        "updated_at": 0,
        "last_hit_at": 0,
        "hit_count": 0,
        "is_deleted": False,
    }
    payload.update(
        {
            key: value
            for key, value in entity.items()
            if key in payload and value is not None
        }
    )
    return LongTermMemory(**payload)


def build_active_memory_filter(
    tenant_id: str,
    user_id: str,
    memory_type: str | None = None,
) -> str:
    """构造长期记忆查询过滤条件。"""
    filters = [
        f'tenant_id == "{tenant_id}"',
        f'user_id == "{user_id}"',
        'is_deleted == false',
    ]
    if memory_type is not None:
        filters.insert(2, f'memory_type == "{memory_type}"')
    return " and ".join(filters)


def build_memory_record(
    *,
    memory_id: str,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    embedding: list[float],
    now_ts: int,
) -> MilvusRecord:
    """构造一条待写入 Milvus 的长期记忆记录。"""
    return {
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


def build_partial_update_record(
    memory_id: str,
    *,
    updated_at: int,
    **fields: Any,
) -> MilvusRecord:
    """构造 Milvus partial upsert 记录。"""
    record: MilvusRecord = {
        "memory_id": memory_id,
        "updated_at": updated_at,
    }
    record.update(fields)
    return record


def search_results_from_hits(hits: Sequence[_MilvusHit]) -> list[MemorySearchResult]:
    """把检索命中统一转换为领域层搜索结果。"""
    search_results: list[MemorySearchResult] = []
    for hit in hits:
        entity = hit.get("entity")
        if not isinstance(entity, dict):
            continue
        search_results.append(
            MemorySearchResult(
                memory=entity_to_memory(entity),
                score=hit.get("score", 0.0),
            )
        )
    return search_results


def has_dedup_match(
    result_groups,
    similarity_threshold: float,
) -> bool:
    """判断去重检索结果里是否已有足够相似的记忆。"""
    if not result_groups or not result_groups[0]:
        return False

    max_score = max(item.get("distance", 0) for item in result_groups[0])
    return max_score >= similarity_threshold


class _HitUpdatePlan(TypedDict):
    """长期记忆命中后的更新计划。"""

    hit_count: int
    last_hit_at: int
    update_record: MilvusRecord


def build_new_memory_insert_record(
    *,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    embedding: list[float],
    now_ts: int,
    memory_id: str | None = None,
) -> tuple[str, MilvusRecord]:
    """构造一条新长期记忆的写入计划。"""
    resolved_memory_id = memory_id or str(uuid.uuid4())
    record = build_memory_record(
        memory_id=resolved_memory_id,
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=memory_type,
        content=content,
        embedding=embedding,
        now_ts=now_ts,
    )
    return resolved_memory_id, record


def build_hit_update_plan(
    memory: LongTermMemory,
    update_config: dict[str, Any],
    now_ts: int,
) -> _HitUpdatePlan:
    """根据命中更新策略生成 partial upsert payload。"""
    last_hit_at = now_ts if update_config["update_last_hit_at"] else memory.last_hit_at
    hit_count = (
        (memory.hit_count or 0) + 1
        if update_config["increase_hit_count"]
        else memory.hit_count
    )
    update_record = build_partial_update_record(
        memory.memory_id,
        updated_at=now_ts,
        hit_count=hit_count,
        last_hit_at=last_hit_at,
    )
    return {
        "hit_count": hit_count,
        "last_hit_at": last_hit_at,
        "update_record": update_record,
    }


def preview_text(text: str, limit: int) -> str:
    """为日志截断长文本，避免低价值噪音。"""
    return text[:limit]


def resolve_active_search_request(
    search_config: dict[str, Any],
    tenant_id: str,
    user_id: str,
    top_k: int | None,
    score_threshold: float | None,
) -> tuple[str, int, float]:
    """统一补齐活跃记忆过滤条件与检索参数。"""
    resolved_top_k = top_k if top_k is not None else search_config["top_k"]
    resolved_score_threshold = (
        score_threshold
        if score_threshold is not None
        else search_config["score_threshold"]
    )
    filter_expr = build_active_memory_filter(tenant_id, user_id)
    return filter_expr, resolved_top_k, resolved_score_threshold


def create_default_retrieval_core(
    *,
    milvus_client: Any,
    embedding_model: Any,
    collection_name: str,
) -> MilvusHybridSearchCore:
    """创建默认的 Milvus 混合检索核心。"""
    return MilvusHybridSearchCore(
        milvus_client=milvus_client,
        embedding_model=embedding_model,
        collection_name=collection_name,
        dense_field="embedding",
        sparse_field="sparse_vector",
        dense_metric_type="COSINE",
        dense_search_params={"nprobe": 16},
        hybrid_rrf_k=60,
    )


def ensure_collection_ready_or_raise(
    *,
    milvus_client: Any,
    collection_name: str,
    logger: LoggerLike,
) -> None:
    """确保长期记忆 collection 已就绪；失败时统一补充上下文日志。"""
    try:
        created = ensure_memory_collection(
            milvus_client,
            collection_name,
        )
        if not created:
            logger.info(f"Collection {collection_name} 已存在")
            return
        logger.info(f"Collection {collection_name} 创建成功（含 BM25 全文索引）")
    except Exception as exc:
        logger.error(
            f"创建 Collection {collection_name} 失败 | {exc}",
            exc_info=True,
        )
        raise


async def save_memory_record(
    *,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    get_embedding: EmbeddingGetter,
    now_ts: _NowProvider,
    logger: LoggerLike,
    build_record: Callable[..., tuple[str, MilvusRecord]],
    insert_records: Callable[[Any, str, Sequence[MilvusRecord]], None],
    milvus_client: Any,
    collection_name: str,
) -> str | None:
    """执行长期记忆保存流程。"""
    try:
        embedding = await get_embedding(content)
        if not embedding:
            logger.warning(
                f"保存记忆失败：embedding 生成返回空 | tenant={tenant_id} "
                f"user={user_id} type={memory_type}"
            )
            return None

        memory_id, memory_data = build_record(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            embedding=embedding,
            now_ts=now_ts(),
        )
        insert_records(milvus_client, collection_name, [memory_data])
        return memory_id
    except Exception as exc:
        logger.error(
            f"保存记忆异常 | tenant={tenant_id} user={user_id} "
            f"type={memory_type} | {exc}",
            exc_info=True,
        )
        return None


async def update_memory_hit_record(
    *,
    memory: LongTermMemory,
    update_on_hit_config: dict[str, Any],
    now_ts: _NowProvider,
    logger: LoggerLike,
    build_hit_update_plan: Callable[[LongTermMemory, dict[str, Any], int], dict[str, Any]],
    upsert_records: Callable[[Any, str, Sequence[MilvusRecord]], None],
    milvus_client: Any,
    collection_name: str,
) -> bool:
    """执行命中计数更新流程。"""
    try:
        if not update_on_hit_config["enabled"]:
            return True

        update_plan = build_hit_update_plan(memory, update_on_hit_config, now_ts())
        memory.hit_count = update_plan["hit_count"]
        memory.last_hit_at = update_plan["last_hit_at"]
        upsert_records(milvus_client, collection_name, [update_plan["update_record"]])
        return True
    except Exception as exc:
        logger.error(
            f"update_memory_hit_info 异常 | memory_id={memory.memory_id} | {exc}",
            exc_info=True,
        )
        return False


async def deduplicate_memory_content(
    *,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    get_embedding: EmbeddingGetter,
    logger: LoggerLike,
    build_active_memory_filter: Callable[[str, str, str | None], str],
    deduplication_config: dict[str, Any],
    dedup_output_fields: list[str],
    milvus_client: Any,
    collection_name: str,
) -> bool:
    """执行新增前去重检查。"""
    try:
        embedding = await get_embedding(content)
        if not embedding:
            return False

        filter_expr = build_active_memory_filter(tenant_id, user_id, memory_type)
        results = search_records(
            milvus_client,
            collection_name,
            embedding,
            filter_expr,
            limit=deduplication_config["top_k"],
            output_fields=dedup_output_fields,
        )
        return not has_dedup_match(
            results,
            deduplication_config["similarity_threshold"],
        )
    except Exception as exc:
        logger.error(
            f"deduplicate_memory 异常 | tenant={tenant_id} "
            f"user={user_id} type={memory_type} | {exc}",
            exc_info=True,
        )
        return False


async def search_active_hybrid_memories(
    *,
    tenant_id: str,
    user_id: str,
    query: str,
    top_k: int | None,
    score_threshold: float | None,
    search_config: dict[str, Any],
    retrieval_core: Any,
    output_fields: list[str],
    resolve_active_search: Callable[
        [dict[str, Any], str, str, int | None, float | None],
        tuple[str, int, float],
    ],
    preview_text: Callable[[str, int], str],
    logger: LoggerLike,
    search_log_preview_limit: int,
    search_limit_multiplier: int,
) -> list[MemorySearchResult]:
    """执行“活跃长期记忆”的混合检索流程。"""
    try:
        filter_expr, resolved_top_k, resolved_score_threshold = resolve_active_search(
            search_config,
            tenant_id,
            user_id,
            top_k,
            score_threshold,
        )
        hits = await retrieval_core.search_hybrid(
            query,
            limit=resolved_top_k,
            filter_expr=filter_expr,
            output_fields=output_fields,
            score_threshold=resolved_score_threshold,
            search_limit=resolved_top_k * search_limit_multiplier,
        )
        return search_results_from_hits(hits)
    except Exception as exc:
        logger.error(
            f"hybrid_search 异常 | tenant={tenant_id} user={user_id} "
            f"query={preview_text(query, search_log_preview_limit)} | {exc}",
            exc_info=True,
        )
        return []


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
        ensure_collection_ready_or_raise(
            milvus_client=self.milvus_client,
            collection_name=self.collection_name,
            logger=logger,
        )
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
        return await save_memory_record(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            get_embedding=self._get_embedding,
            now_ts=self._now_ts,
            logger=logger,
            build_record=build_new_memory_insert_record,
            insert_records=insert_records,
            milvus_client=self.milvus_client,
            collection_name=self.collection_name,
        )

    async def update_memory_hit_info(self, memory: LongTermMemory) -> bool:
        """
        使用 Milvus partial_update 更新命中计数器。

        只传 memory_id + hit_count + last_hit_at + updated_at（部分更新），
        不需要重新生成 embedding，也不需要传输全量字段。
        """
        return await update_memory_hit_record(
            memory=memory,
            update_on_hit_config=self.update_on_hit_config,
            now_ts=self._now_ts,
            logger=logger,
            build_hit_update_plan=build_hit_update_plan,
            upsert_records=upsert_records,
            milvus_client=self.milvus_client,
            collection_name=self.collection_name,
        )

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
        return await deduplicate_memory_content(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            get_embedding=self._get_embedding,
            logger=logger,
            build_active_memory_filter=build_active_memory_filter,
            deduplication_config=self.deduplication_config,
            dedup_output_fields=DEDUP_OUTPUT_FIELDS,
            milvus_client=self.milvus_client,
            collection_name=self.collection_name,
        )

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
        return await search_active_hybrid_memories(
            tenant_id=tenant_id,
            user_id=user_id,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            search_config=self.search_config,
            retrieval_core=self.retrieval_core,
            output_fields=MEMORY_OUTPUT_FIELDS,
            resolve_active_search=resolve_active_search_request,
            preview_text=preview_text,
            logger=logger,
            search_log_preview_limit=SEARCH_LOG_PREVIEW_LIMIT,
            search_limit_multiplier=HYBRID_SEARCH_LIMIT_MULTIPLIER,
        )

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
