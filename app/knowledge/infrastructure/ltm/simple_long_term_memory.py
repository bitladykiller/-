"""Milvus 长期记忆存储层。

这个模块负责：
- 管理长期记忆的 Milvus collection 与索引
- 提供混合检索、去重、命中刷新和软删除能力

这个模块不负责：
- 对话级编排
- LLM 抽取逻辑
- Prompt 构造

分层约定：
模块级函数只放**纯函数**（过滤表达式拼装、记录构造、命中转换），
它们无 IO、可单测、可被别处复用；带 IO 的编排一律是 `SimpleLongTermMemory`
的方法，直接使用实例上的 client / config，不再把 logger、写入函数这类
恒定依赖当参数层层下传。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias

from app.knowledge.domain.schemas import LongTermMemory, MemorySearchResult
from app.knowledge.infrastructure.ltm.ltm_collection import (
    DEDUP_OUTPUT_FIELDS,
    MEMORY_OUTPUT_FIELDS,
    MilvusRecord,
    build_primary_key_in_filter,
    delete_by_filter,
    ensure_memory_collection,
    insert_records,
    query_records,
    search_records,
    upsert_records,
)
from app.shared.core.app_config import (
    LTMDeduplicationConfig,
    LTMSearchConfig,
    LTMUpdateOnHitConfig,
)
from app.shared.core.async_bridge import run_blocking
from app.shared.core.config import settings
from app.shared.core.degradation import log_degradation
from app.shared.core.logger import get_logger
from app.shared.retrieval import MilvusHybridSearchCore
from pymilvus import MilvusClient
from typing_extensions import TypedDict

logger = get_logger(__name__)

SEARCH_LOG_PREVIEW_LIMIT = 100
EMBEDDING_LOG_PREVIEW_LIMIT = 200
HYBRID_SEARCH_LIMIT_MULTIPLIER = 2
#: Milvus query 单次返回上限
MAX_QUERY_LIMIT = 16384

LoggerLike: TypeAlias = Any
_MilvusHit: TypeAlias = Mapping[str, Any]


class _HitUpdatePlan(TypedDict):
    """长期记忆命中后的更新计划。"""

    hit_count: int
    last_hit_at: int
    update_record: MilvusRecord


# ---------------------------------------------------------------------- #
# 纯函数：过滤表达式、记录构造、结果转换
# ---------------------------------------------------------------------- #


def entity_to_memory(entity: Mapping[str, Any]) -> LongTermMemory:
    """将 Milvus entity 字典转换为 LongTermMemory 对象。"""
    payload: dict[str, Any] = {
        "memory_id": "",
        "tenant_id": "",
        "user_id": "",
        "session_id": "",
        "memory_type": "",
        "content": "",
        "created_at": 0,
        "updated_at": 0,
        "last_hit_at": 0,
        "hit_count": 0,
        "is_deleted": False,
    }
    payload.update(
        {key: value for key, value in entity.items() if key in payload and value is not None}
    )
    return LongTermMemory(**payload)


def build_active_memory_filter(
    tenant_id: str,
    user_id: str,
    memory_type: str | None = None,
) -> str:
    """构造"未软删的某用户记忆"过滤条件。"""
    filters = [
        f'tenant_id == "{tenant_id}"',
        f'user_id == "{user_id}"',
        "is_deleted == false",
    ]
    if memory_type is not None:
        filters.insert(2, f'memory_type == "{memory_type}"')
    return " and ".join(filters)


def build_session_memory_filter(tenant_id: str, user_id: str, session_id: str) -> str:
    """构造"某会话下未软删记忆"过滤条件。"""
    return (
        f'tenant_id == "{tenant_id}" and '
        f'user_id == "{user_id}" and '
        f'session_id == "{session_id}" and '
        "is_deleted == false"
    )


def build_expired_soft_deleted_filter(cutoff_ts: int) -> str:
    """构造"已软删且超过保留期"过滤条件。

    软删时会刷新 `updated_at`，故可用它近似软删发生时间。
    """
    return f"is_deleted == true and updated_at < {cutoff_ts}"


def build_memory_record(
    *,
    memory_id: str,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    embedding: list[float],
    now_ts: int,
    session_id: str = "",
) -> MilvusRecord:
    """构造一条待写入 Milvus 的长期记忆记录。"""
    return {
        "memory_id": memory_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "session_id": session_id,
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


def build_new_memory_insert_record(
    *,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    embedding: list[float],
    now_ts: int,
    memory_id: str | None = None,
    session_id: str = "",
) -> tuple[str, MilvusRecord]:
    """构造一条新长期记忆的写入计划（分配 memory_id + 组装记录）。"""
    resolved_memory_id = memory_id or str(uuid.uuid4())
    record = build_memory_record(
        memory_id=resolved_memory_id,
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=memory_type,
        content=content,
        embedding=embedding,
        now_ts=now_ts,
        session_id=session_id,
    )
    return resolved_memory_id, record


def build_hit_update_plan(
    memory: LongTermMemory,
    update_config: LTMUpdateOnHitConfig,
    now_ts: int,
) -> _HitUpdatePlan:
    """根据命中更新策略生成 partial upsert payload。"""
    last_hit_at = now_ts if update_config.update_last_hit_at else memory.last_hit_at
    hit_count = (
        (memory.hit_count or 0) + 1 if update_config.increase_hit_count else memory.hit_count
    )
    return {
        "hit_count": hit_count,
        "last_hit_at": last_hit_at,
        "update_record": build_partial_update_record(
            memory.memory_id,
            updated_at=now_ts,
            hit_count=hit_count,
            last_hit_at=last_hit_at,
        ),
    }


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


def has_dedup_match(result_groups: Any, similarity_threshold: float) -> bool:
    """判断去重检索结果里是否已有足够相似的记忆。"""
    if not result_groups or not result_groups[0]:
        return False
    return max(item.get("distance", 0) for item in result_groups[0]) >= similarity_threshold


def resolve_active_search_request(
    search_config: LTMSearchConfig,
    tenant_id: str,
    user_id: str,
    top_k: int | None,
    score_threshold: float | None,
) -> tuple[str, int, float]:
    """统一补齐活跃记忆过滤条件与检索参数（显式入参优先于配置默认值）。"""
    resolved_top_k = top_k if top_k is not None else search_config.top_k
    resolved_score_threshold = (
        score_threshold if score_threshold is not None else search_config.score_threshold
    )
    return build_active_memory_filter(tenant_id, user_id), resolved_top_k, resolved_score_threshold


def preview_text(text: str, limit: int) -> str:
    """为日志截断长文本，避免低价值噪音。"""
    return text[:limit]


def extract_ids(rows: Sequence[Any], field: str) -> list[str]:
    """从 Milvus query 结果里提取非空主键，跳过异常行。"""
    return [str(row[field]) for row in rows if isinstance(row, dict) and row.get(field)]


# ---------------------------------------------------------------------- #
# 构造期辅助（只在 __init__ 执行一次，不在请求路径上）
# ---------------------------------------------------------------------- #


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
        created = ensure_memory_collection(milvus_client, collection_name)
    except Exception as exc:
        logger.error(f"创建 Collection {collection_name} 失败 | {exc}", exc_info=True)
        raise

    if created:
        logger.info(f"Collection {collection_name} 创建成功（含 BM25 全文索引）")
    else:
        logger.info(f"Collection {collection_name} 已存在")


# ---------------------------------------------------------------------- #
# 存储层
# ---------------------------------------------------------------------- #


class SimpleLongTermMemory:
    """简化版长期记忆模块。

    LTM = Long-Term Memory，长期记忆。
    作用：
    1. 向 Milvus 写入用户长期记忆。
    2. 根据用户当前问题检索长期记忆。
    3. 命中长期记忆后刷新 last_hit_at 和 hit_count。
    4. 会话删除时软删关联记忆，并按保留期物理清理。

    所有 Milvus / embedding 调用都是同步 SDK，统一经线程池 await，
    不会阻塞事件循环（见 `app.shared.core.async_bridge`）。
    """

    def __init__(
        self,
        milvus_client: MilvusClient,
        embedding_model,
        collection_name: str | None = None,
        retrieval_core: MilvusHybridSearchCore | None = None,
    ):
        """初始化长期记忆模块。

        Args:
            milvus_client: Milvus 客户端。
            embedding_model: Embedding 模型，需要有 `embed_query` 方法。
            collection_name: Collection 名称，默认从配置读取。
            retrieval_core: 可选的检索核心注入点，便于单测或替换底层检索实现。
        """
        self.milvus_client = milvus_client
        self.embedding_model = embedding_model
        ltm_config = settings.app_config.memory.ltm
        # 这三个配置是 frozen dataclass，必须用属性访问。
        # 显式标注类型，让"误当成 dict 下标取值"在类型检查阶段就暴露——
        # 历史上这里正是被 `# type: ignore` 掩盖成了运行期 TypeError，
        # 又被宽泛的 except 吞掉，导致长期记忆整条链路静默失效。
        self.search_config: LTMSearchConfig = ltm_config.search
        self.deduplication_config: LTMDeduplicationConfig = ltm_config.deduplication
        self.update_on_hit_config: LTMUpdateOnHitConfig = ltm_config.update_on_hit
        self.collection_name = collection_name or ltm_config.collection_name

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

    @staticmethod
    def _now_ts() -> int:
        """统一生成秒级时间戳。"""
        return int(time.time())

    async def _get_embedding(self, text: str) -> list[float] | None:
        """获取文本的 embedding 向量，失败返回 None。"""
        try:
            return await run_blocking(self.embedding_model.embed_query, text)
        except Exception as exc:
            log_degradation(
                logger,
                "ltm.get_embedding",
                exc,
                text_preview=preview_text(text, EMBEDDING_LOG_PREVIEW_LIMIT),
            )
            return None

    # ------------------------------------------------------------------ #
    # 记忆写入
    # ------------------------------------------------------------------ #

    async def save_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
        *,
        session_id: str = "",
        memory_id: str | None = None,
    ) -> str | None:
        """保存长期记忆。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            memory_type: 记忆类型。
            content: 记忆内容。
            session_id: 关联会话 ID（可选，用于会话级清理）。

        Returns:
            成功返回 memory_id，失败返回 None。
        """
        try:
            embedding = await self._get_embedding(content)
            if not embedding:
                logger.warning(
                    f"保存记忆失败：embedding 生成返回空 | tenant={tenant_id} "
                    f"user={user_id} type={memory_type}"
                )
                return None

            is_idempotent_write = memory_id is not None
            memory_id, record = build_new_memory_insert_record(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_type=memory_type,
                content=content,
                embedding=embedding,
                now_ts=self._now_ts(),
                session_id=session_id,
                memory_id=memory_id,
            )
            if is_idempotent_write:
                await upsert_records(self.milvus_client, self.collection_name, [record])
            else:
                await insert_records(self.milvus_client, self.collection_name, [record])
            return memory_id
        except Exception as exc:
            log_degradation(
                logger,
                "ltm.save_memory",
                exc,
                tenant=tenant_id,
                user=user_id,
                type=memory_type,
            )
            return None

    async def deduplicate_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
    ) -> bool:
        """新增前的去重检查。

        Returns:
            True 表示需要新增（没有足够相似的记忆）；False 表示已存在或检查失败。
        """
        try:
            embedding = await self._get_embedding(content)
            if not embedding:
                return False

            results = await search_records(
                self.milvus_client,
                self.collection_name,
                embedding,
                build_active_memory_filter(tenant_id, user_id, memory_type),
                limit=self.deduplication_config.top_k,
                output_fields=DEDUP_OUTPUT_FIELDS,
            )
            return not has_dedup_match(
                results,
                self.deduplication_config.similarity_threshold,
            )
        except Exception as exc:
            log_degradation(
                logger,
                "ltm.deduplicate_memory",
                exc,
                tenant=tenant_id,
                user=user_id,
                type=memory_type,
            )
            return False

    async def update_memory_hit_info(self, memory: LongTermMemory) -> bool:
        """刷新单条记忆的命中计数器。

        只 upsert memory_id + hit_count + last_hit_at + updated_at（部分更新），
        不重新生成 embedding，也不传输全量字段。
        """
        return await self.update_memory_hit_infos([memory])

    async def update_memory_hit_infos(self, memories: Sequence[LongTermMemory]) -> bool:
        """批量刷新命中计数器。

        WHY 批量：一次检索通常命中多条记忆，逐条 upsert 就是逐条 Milvus 往返。
        命中统计是旁路逻辑，不值得为它付 N 次 RTT。
        """
        if not self.update_on_hit_config.enabled or not memories:
            return True

        try:
            now_ts = self._now_ts()
            records: list[MilvusRecord] = []
            for memory in memories:
                plan = build_hit_update_plan(memory, self.update_on_hit_config, now_ts)
                memory.hit_count = plan["hit_count"]
                memory.last_hit_at = plan["last_hit_at"]
                records.append(plan["update_record"])

            await upsert_records(self.milvus_client, self.collection_name, records)
            return True
        except Exception as exc:
            log_degradation(logger, "ltm.update_memory_hit_infos", exc, count=len(memories))
            return False

    async def update_memory_hit_infos_deduped(
        self,
        memories: Sequence[LongTermMemory],
        *,
        turn_id: str = "",
    ) -> bool:
        """批量刷新命中计数器（带去重保护）。

        v3.36+: 通过 memory_hit_events (turn_id, memory_id) 唯一索引去重——
        同一 turn 同一条记忆命中，只有首次 INSERT 成功才增加 hit_count。
        MySQL 不可用时静默回退到无去重模式（原有行为）。

        重要：Milvus partial upsert 不支持原子递增，hit_count 需要
        在内存对象上算好再覆盖。如果 memories 来自 event payload 重建
        （hit_count=0），必须先从 Milvus 读出真实值，否则会把已有
        计数覆盖为 1。
        """
        if not self.update_on_hit_config.enabled or not memories:
            return True

        new_memories: list[LongTermMemory] = []
        for memory in memories:
            memory_id = getattr(memory, "memory_id", "")
            if not memory_id:
                continue
            is_new = await self._try_record_hit_event_dedup(
                turn_id,
                memory_id,
                tenant_id=getattr(memory, "tenant_id", "") or "default",
            )
            if is_new:
                new_memories.append(memory)

        if not new_memories:
            return True

        # payload 重建的 memory 对象 hit_count=0，需从 Milvus 读真实值
        if any(getattr(m, "hit_count", 0) == 0 for m in new_memories):
            reloaded = await self._reload_hit_counts(new_memories)
            if reloaded:
                new_memories = reloaded

        return await self.update_memory_hit_infos(new_memories)

    async def _reload_hit_counts(
        self,
        memories: list[LongTermMemory],
    ) -> list[LongTermMemory] | None:
        """从 Milvus 读出每条记忆的真实 hit_count，返回更新后的列表。

        Milvus 不可用时返回 None，调用方使用原对象（容错降级）。
        """
        try:
            memory_ids = [m.memory_id for m in memories]
            id_filter = ", ".join(f'"{mid}"' for mid in memory_ids)
            rows = await query_records(
                self.milvus_client,
                self.collection_name,
                f"memory_id in [{id_filter}]",
                limit=len(memory_ids),
                output_fields=["memory_id", "hit_count", "last_hit_at"],
            )
            id_to_hit: dict[str, tuple[int, int]] = {}
            for row in rows:
                if isinstance(row, dict):
                    mid = str(row.get("memory_id", ""))
                    id_to_hit[mid] = (
                        int(row.get("hit_count", 0)),
                        int(row.get("last_hit_at", 0)),
                    )
            result: list[LongTermMemory] = []
            for m in memories:
                if m.memory_id in id_to_hit:
                    hc, lha = id_to_hit[m.memory_id]
                    m.hit_count = hc
                    m.last_hit_at = lha
                result.append(m)
            return result
        except Exception as exc:
            log_degradation(
                logger,
                "ltm.reload_hit_counts",
                exc,
                count=len(memories),
            )
            return None

    @staticmethod
    async def _try_record_hit_event_dedup(
        turn_id: str,
        memory_id: str,
        *,
        tenant_id: str = "default",
    ) -> bool:
        """尝试记录命中事件去重条目。返回 True 表示首次命中（需计数）。"""
        if not turn_id or not memory_id:
            return True
        try:
            from app.shared.core.database import AsyncSessionLocal
            from sqlalchemy import text
            from sqlalchemy.exc import IntegrityError

            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "INSERT INTO memory_hit_events "
                        "(tenant_id, turn_id, memory_id) "
                        "VALUES (:tnt, :tid, :mid)"
                    ),
                    {"tnt": tenant_id, "tid": turn_id, "mid": memory_id},
                )
                await db.commit()
                return True
        except IntegrityError:
            return False
        except Exception:
            return True

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    async def hybrid_search(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[MemorySearchResult]:
        """混合检索：向量检索 + Milvus BM25 + RRF 融合。

        WHY 用 Milvus 内置 BM25 而不是客户端手动关键词打分：
        - BM25 在服务端计算，IDF 统计来自实际集合，打分更准
        - 不需要把全部记忆拉到客户端
        - 少一轮全量传输，延迟更低

        检索失败时返回空列表——长期记忆是增强项，不应让主对话链路失败。
        """
        try:
            filter_expr, resolved_top_k, resolved_score_threshold = resolve_active_search_request(
                self.search_config,
                tenant_id,
                user_id,
                top_k,
                score_threshold,
            )
            hits = await self.retrieval_core.search_hybrid(
                query,
                limit=resolved_top_k,
                filter_expr=filter_expr,
                output_fields=MEMORY_OUTPUT_FIELDS,
                score_threshold=resolved_score_threshold,
                search_limit=resolved_top_k * HYBRID_SEARCH_LIMIT_MULTIPLIER,
            )
            return search_results_from_hits(hits)
        except Exception as exc:
            log_degradation(
                logger,
                "ltm.hybrid_search",
                exc,
                tenant=tenant_id,
                user=user_id,
                query=preview_text(query, SEARCH_LOG_PREVIEW_LIMIT),
            )
            return []

    # ------------------------------------------------------------------ #
    # 删除与清理
    # ------------------------------------------------------------------ #

    async def soft_delete_session_memories(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> int:
        """软删除指定会话关联的长期记忆。

        依赖动态字段 session_id（enable_dynamic_field=True）。
        历史无 session_id 的记录不会被匹配，避免误删跨会话记忆。

        Returns:
            软删条数；失败返回 0。
        """
        if not session_id:
            return 0
        try:
            rows = await query_records(
                self.milvus_client,
                self.collection_name,
                build_session_memory_filter(tenant_id, user_id, session_id),
                limit=MAX_QUERY_LIMIT,
                output_fields=["memory_id"],
            )
            memory_ids = extract_ids(rows, "memory_id")
            if not memory_ids:
                return 0

            now_ts = self._now_ts()
            await upsert_records(
                self.milvus_client,
                self.collection_name,
                [
                    build_partial_update_record(
                        memory_id,
                        updated_at=now_ts,
                        is_deleted=True,
                    )
                    for memory_id in memory_ids
                ],
            )
            return len(memory_ids)
        except Exception as exc:
            log_degradation(
                logger,
                "ltm.soft_delete_session_memories",
                exc,
                tenant=tenant_id,
                user=user_id,
                session=session_id,
            )
            return 0

    async def hard_purge_soft_deleted(
        self,
        *,
        retention_seconds: int | None = None,
        batch_limit: int | None = None,
    ) -> int:
        """物理删除已软删且超过保留期的 LTM 记录。

        Returns:
            删除条数；失败返回 0 并打错误日志。
        """
        purge_cfg = settings.app_config.memory.ltm.purge
        retention = (
            retention_seconds if retention_seconds is not None else purge_cfg.retention_seconds
        )
        limit = batch_limit if batch_limit is not None else purge_cfg.batch_limit
        if retention < 0 or limit <= 0:
            return 0

        cutoff = self._now_ts() - int(retention)
        try:
            rows = await query_records(
                self.milvus_client,
                self.collection_name,
                build_expired_soft_deleted_filter(cutoff),
                limit=limit,
                output_fields=["memory_id"],
            )
            memory_ids = extract_ids(rows, "memory_id")
            if not memory_ids:
                return 0

            await delete_by_filter(
                self.milvus_client,
                self.collection_name,
                build_primary_key_in_filter("memory_id", memory_ids),
            )
            logger.info(
                "LTM 硬清理完成 | deleted=%s cutoff=%s collection=%s",
                len(memory_ids),
                cutoff,
                self.collection_name,
            )
            return len(memory_ids)
        except Exception as exc:
            log_degradation(
                logger, "ltm.hard_purge_soft_deleted", exc, collection=self.collection_name
            )
            return 0


__all__ = [
    "DEDUP_OUTPUT_FIELDS",
    "MEMORY_OUTPUT_FIELDS",
    "SimpleLongTermMemory",
    "build_active_memory_filter",
    "build_expired_soft_deleted_filter",
    "build_hit_update_plan",
    "build_memory_record",
    "build_new_memory_insert_record",
    "build_partial_update_record",
    "build_session_memory_filter",
    "create_default_retrieval_core",
    "ensure_collection_ready_or_raise",
    "entity_to_memory",
    "extract_ids",
    "has_dedup_match",
    "preview_text",
    "resolve_active_search_request",
    "search_results_from_hits",
]
