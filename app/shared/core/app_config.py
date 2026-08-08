"""统一应用配置 — 聚合基础设施、业务和运行时行为配置。

职责：
- 作为所有配置项的单一入口
- 将代码中的硬编码常量集中管理
- 提供分层的配置访问接口
- 统一管理 STM/LTM 配置（合并自 knowledge/infrastructure/config/）

不负责：
- 环境变量解析（由 config_models.py 的 BaseSettings 处理；
  个别字段的定向 env 覆盖用 default_factory 显式声明）
- 连接 URL 拼接（由 config.py 处理）

WHY 用 pydantic BaseModel 而不是 dataclass：
- **启动时校验**：字段类型/取值错误在进程启动即失败，而不是运行期才炸。
  本项目吃过亏——LTM 配置被当 dict 下标取值，TypeError 被吞，长期记忆
  静默失效很久；dataclass 给不了任何早期信号。
- frozen 语义与 dataclass 等价（`model_config = frozen`），属性访问不变，
  关键字构造不变，测试与调用方零改动。
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenConfig(BaseModel):
    """全部配置节的公共基类：不可变 + 禁止未知字段。

    `extra="forbid"`：拼错字段名（如 yaml/env 注入时）直接报错，
    而不是被静默忽略后表现为"改了配置不生效"。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# ====================================================================
# ReAct 配置
# ====================================================================


class ReactConfig(FrozenConfig):
    """ReAct 兜底执行策略的运行时配置。"""

    max_attempts: int = 5
    recursion_limit: int = 11
    transcript_window: int = 20
    progress_message: str = "正在综合分析..."
    fallback_answer: str = "亲～这个问题回答不了哦～"
    retry_prompt: str = "上一次候选答案仍然不充分，请继续按标准 ReAct 检索并补足关键事实。"
    step_exhausted_marker: str = "need more steps"
    step_exhausted_reason: str = "单次 ReAct 内部步数耗尽，仍未得到足够答案。"
    default_insufficiency_reason: str = "答案信息不足。"
    initial_reason: str = "初始状态：尚未完成充分回答。"


# ====================================================================
# 文档上传配置
# ====================================================================


def _default_upload_dir() -> str:
    """上传目录，可用环境变量 UPLOAD_DIR 覆盖。

    WHY 必须可配置：这里曾经硬编码相对路径 "uploads"，落盘位置随进程 CWD 漂移。
    Docker 下 start.sh 在 /app 启动 → 文件写进 /app/uploads（容器可写层），
    而持久卷挂在 /app/app/uploads —— 容器一重建，上传的原始文件全部丢失，
    Milvus 索引和 MySQL 元数据却还在。`.env.docker` 已把本变量对齐到卷挂载点。
    """
    return os.getenv("UPLOAD_DIR", "uploads")


class UploadConfig(FrozenConfig):
    """文档上传的运行时配置。"""

    max_upload_size_mb: int = Field(default=50, gt=0)
    upload_dir: str = Field(default_factory=_default_upload_dir)

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


# ====================================================================
# 任务队列配置
# ====================================================================


class TaskQueueConfig(FrozenConfig):
    """后台任务状态存储配置。"""

    task_key_prefix: str = "task:doc_parse:"
    task_ttl_seconds: int = Field(default=3600 * 24, gt=0)


# ====================================================================
# 短期记忆 (STM) 配置 — 合并自 knowledge/infrastructure/config
# ====================================================================


class STMRedisConfig(FrozenConfig):
    """短期记忆的 Redis 相关配置。"""

    key_prefix: str = "agent:stm"
    ttl_seconds: int = Field(default=86400, gt=0)
    lock_ttl_seconds: int = Field(default=10, gt=0)


class STMWindowConfig(FrozenConfig):
    """短期记忆的消息窗口配置。"""

    max_messages: int = Field(default=16, gt=0)


class STMCompressionConfig(FrozenConfig):
    """短期记忆压缩阈值配置。"""

    enabled: bool = True
    trigger_rounds: int = Field(default=6, gt=0)
    trigger_messages: int = Field(default=20, gt=0)
    keep_recent_rounds: int = Field(default=4, gt=0)


class STMConfig(FrozenConfig):
    """短期记忆总配置。"""

    enabled: bool = True
    time_window_seconds: int = Field(default=86400, gt=0)
    redis: STMRedisConfig = Field(default_factory=STMRedisConfig)
    window: STMWindowConfig = Field(default_factory=STMWindowConfig)
    compression: STMCompressionConfig = Field(default_factory=STMCompressionConfig)


# ====================================================================
# 长期记忆 (LTM) 配置 — 合并自 knowledge/infrastructure/config
# ====================================================================


class LTMSearchConfig(FrozenConfig):
    """长期记忆检索配置。"""

    top_k: int = Field(default=5, gt=0)
    score_threshold: float = Field(default=0.72, ge=0.0, le=1.0)


class LTMDeduplicationConfig(FrozenConfig):
    """长期记忆去重配置。"""

    top_k: int = Field(default=3, gt=0)
    similarity_threshold: float = Field(default=0.88, ge=0.0, le=1.0)


class LTMUpdateOnHitConfig(FrozenConfig):
    """长期记忆命中后更新策略。"""

    enabled: bool = True
    update_last_hit_at: bool = True
    increase_hit_count: bool = True


class LTMPurgeConfig(FrozenConfig):
    """已软删 LTM 的定时硬清理配置。

    业务删会话仍走 soft_delete；本任务定期把 is_deleted=true
    且超过保留期的记录从 Milvus 物理删除，回收空间。
    """

    enabled: bool = True
    # 调度间隔（秒）：默认 1 小时
    interval_seconds: int = Field(default=3600, gt=0)
    # 软删后至少保留多久再硬删（秒）：默认 7 天
    retention_seconds: int = Field(default=7 * 24 * 3600, ge=0)
    # 单次 query 上限（与 soft_delete 一致）
    batch_limit: int = Field(default=16384, gt=0)


def _default_ltm_collection_name() -> str:
    """LTM collection 名，可用环境变量 MILVUS_COLLECTION_NAME 覆盖。

    WHY 收敛在这里：此前 env `MILVUS_COLLECTION_NAME`（容器装配用）与
    本配置项是**两个真相来源**，改一处不改另一处会静默分家。
    现在 env 只是本字段的默认值来源，容器统一读 `app_config.memory.ltm.collection_name`。
    """
    return os.getenv("MILVUS_COLLECTION_NAME", "customer_agent_long_memory")


class LTMConfig(FrozenConfig):
    """长期记忆总配置。"""

    enabled: bool = True
    collection_name: str = Field(default_factory=_default_ltm_collection_name)
    search: LTMSearchConfig = Field(default_factory=LTMSearchConfig)
    deduplication: LTMDeduplicationConfig = Field(default_factory=LTMDeduplicationConfig)
    update_on_hit: LTMUpdateOnHitConfig = Field(default_factory=LTMUpdateOnHitConfig)
    purge: LTMPurgeConfig = Field(default_factory=LTMPurgeConfig)


# ====================================================================
# 记忆系统配置
# ====================================================================

LTM_MEMORY_TYPES: dict[str, str] = {
    "ISSUE_HISTORY": "issue_history",
    "SOLUTION_NOTE": "solution_note",
}

SENSITIVE_PATTERNS: tuple[str, ...] = (
    r"password|密码|passwd",
    r"验证码|verification.code|captcha",
    r"\d{17}[\dXx]",
    r"\d{16,19}",
    r"token|secret|access.key|api.key",
    r"1[3-9]\d{9}",
)


class MemoryConfig(FrozenConfig):
    """记忆系统的运行时配置。"""

    memory_extractor_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    user_profile_cache_ttl: int = Field(default=1800, gt=0)
    stm: STMConfig = Field(default_factory=STMConfig)
    ltm: LTMConfig = Field(default_factory=LTMConfig)
    ltm_memory_types: dict[str, str] = Field(default_factory=lambda: dict(LTM_MEMORY_TYPES))
    sensitive_patterns: tuple[str, ...] = Field(default_factory=lambda: SENSITIVE_PATTERNS)


# ====================================================================
# RAG 查询改写（仅书面化；不做 HYDE / 退步）
# ====================================================================


class RagRewriteConfig(FrozenConfig):
    """RAG 查询书面化改写配置。

    仅作用于文档检索支路；图谱检索不使用。
    """

    # 是否在进入 HybridSearcher 前做书面化改写
    formalize_enabled: bool = True
    # LLM 超时（秒）；超时或失败则回退原问句
    timeout_seconds: float = Field(default=3.0, gt=0)
    # 改写结果最大字符数，防止异常长输出
    max_chars: int = Field(default=256, gt=0)


# ====================================================================
# 资源护栏与知识分域
# ====================================================================


class LimitsConfig(FrozenConfig):
    """资源护栏配置。"""

    #: 单用户同时进行的流式问答上限
    sse_max_concurrent_per_user: int = Field(default=3, gt=0)
    #: 单租户同时进行的流式问答上限（SaaS 企业级配额；0 = 不启用）
    sse_max_concurrent_per_tenant: int = Field(default=0, ge=0)
    #: 并发槽位兜底 TTL（秒）：进程崩溃未释放时的自动回收窗口
    sse_slot_ttl_seconds: int = Field(default=300, gt=0)


class RagVisibilityConfig(FrozenConfig):
    """知识库分域开关（owner_id 过滤）。

    默认关闭：现网集合的存量 chunk 没有 owner_id 字段，直接开过滤会把
    它们全部排除。开启前必须完成一次全量 reindex（见 05 文档）。
    """

    enabled: bool = False
    #: 共享域标识：所有用户可检索
    global_owner: str = "global"

    @field_validator("global_owner")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("global_owner 不能为空")
        return value


# ====================================================================
# 聚合配置
# ====================================================================


class AppConfig(FrozenConfig):
    """应用级统一配置。

    所有硬编码常量收敛到此结构，不再分散在各模块中。
    """

    react: ReactConfig = Field(default_factory=ReactConfig)
    upload: UploadConfig = Field(default_factory=UploadConfig)
    task_queue: TaskQueueConfig = Field(default_factory=TaskQueueConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    rag_rewrite: RagRewriteConfig = Field(default_factory=RagRewriteConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    rag_visibility: RagVisibilityConfig = Field(default_factory=RagVisibilityConfig)


app_config = AppConfig()

__all__ = [
    "AppConfig",
    "LimitsConfig",
    "RagVisibilityConfig",
    "LTMConfig",
    "LTMDeduplicationConfig",
    "LTMPurgeConfig",
    "LTMSearchConfig",
    "LTMUpdateOnHitConfig",
    "MemoryConfig",
    "RagRewriteConfig",
    "ReactConfig",
    "STMCompressionConfig",
    "STMConfig",
    "STMRedisConfig",
    "STMWindowConfig",
    "TaskQueueConfig",
    "UploadConfig",
    "app_config",
]
