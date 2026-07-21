"""统一应用配置 — 聚合基础设施、业务和运行时行为配置。

职责：
- 作为所有配置项的单一入口
- 将代码中的硬编码常量集中管理
- 提供分层的配置访问接口
- 统一管理 STM/LTM 配置（合并自 knowledge/infrastructure/config/）

不负责：
- 环境变量解析（由 config_models.py 的 BaseSettings 处理）
- 连接 URL 拼接（由 config.py 处理）
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ====================================================================
# ReAct 配置
# ====================================================================

@dataclass(frozen=True)
class ReactConfig:
    """ReAct 兜底执行策略的运行时配置。"""

    max_attempts: int = 5
    recursion_limit: int = 11
    transcript_window: int = 20
    progress_message: str = "正在综合分析..."
    fallback_answer: str = "亲～这个问题回答不了哦～"
    retry_prompt: str = (
        "上一次候选答案仍然不充分，请继续按标准 ReAct 检索并补足关键事实。"
    )
    step_exhausted_marker: str = "need more steps"
    step_exhausted_reason: str = "单次 ReAct 内部步数耗尽，仍未得到足够答案。"
    default_insufficiency_reason: str = "答案信息不足。"
    initial_reason: str = "初始状态：尚未完成充分回答。"


# ====================================================================
# 文档上传配置
# ====================================================================

@dataclass(frozen=True)
class UploadConfig:
    """文档上传的运行时配置。"""

    max_upload_size_mb: int = 50
    max_upload_size_bytes: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_upload_size_bytes",
            self.max_upload_size_mb * 1024 * 1024,
        )


# ====================================================================
# 任务队列配置
# ====================================================================

@dataclass(frozen=True)
class TaskQueueConfig:
    """异步任务队列的运行时配置。"""

    task_key_prefix: str = "task:doc_parse:"
    task_ttl_seconds: int = 3600 * 24


# ====================================================================
# 短期记忆 (STM) 配置 — 合并自 knowledge/infrastructure/config
# ====================================================================

@dataclass(frozen=True)
class STMRedisConfig:
    """短期记忆的 Redis 相关配置。"""

    key_prefix: str = "agent:stm"
    ttl_seconds: int = 86400
    lock_ttl_seconds: int = 10


@dataclass(frozen=True)
class STMWindowConfig:
    """短期记忆的消息窗口配置。"""

    max_messages: int = 16


@dataclass(frozen=True)
class STMCompressionConfig:
    """短期记忆压缩阈值配置。"""

    enabled: bool = True
    trigger_rounds: int = 6
    trigger_messages: int = 20
    keep_recent_rounds: int = 4


@dataclass(frozen=True)
class STMConfig:
    """短期记忆总配置。"""

    enabled: bool = True
    time_window_seconds: int = 86400
    redis: STMRedisConfig = field(default_factory=STMRedisConfig)
    window: STMWindowConfig = field(default_factory=STMWindowConfig)
    compression: STMCompressionConfig = field(default_factory=STMCompressionConfig)


# ====================================================================
# 长期记忆 (LTM) 配置 — 合并自 knowledge/infrastructure/config
# ====================================================================

@dataclass(frozen=True)
class LTMSearchConfig:
    """长期记忆检索配置。"""

    top_k: int = 5
    score_threshold: float = 0.72


@dataclass(frozen=True)
class LTMDeduplicationConfig:
    """长期记忆去重配置。"""

    top_k: int = 3
    similarity_threshold: float = 0.88


@dataclass(frozen=True)
class LTMUpdateOnHitConfig:
    """长期记忆命中后更新策略。"""

    enabled: bool = True
    update_last_hit_at: bool = True
    increase_hit_count: bool = True


@dataclass(frozen=True)
class LTMPurgeConfig:
    """已软删 LTM 的定时硬清理配置。

    业务删会话仍走 soft_delete；本任务定期把 is_deleted=true
    且超过保留期的记录从 Milvus 物理删除，回收空间。
    """

    enabled: bool = True
    # 调度间隔（秒）：默认 1 小时
    interval_seconds: int = 3600
    # 软删后至少保留多久再硬删（秒）：默认 7 天
    retention_seconds: int = 7 * 24 * 3600
    # 单次 query 上限（与 soft_delete 一致）
    batch_limit: int = 16384


@dataclass(frozen=True)
class LTMConfig:
    """长期记忆总配置。"""

    enabled: bool = True
    collection_name: str = "customer_agent_long_memory"
    search: LTMSearchConfig = field(default_factory=LTMSearchConfig)
    deduplication: LTMDeduplicationConfig = field(default_factory=LTMDeduplicationConfig)
    update_on_hit: LTMUpdateOnHitConfig = field(default_factory=LTMUpdateOnHitConfig)
    purge: LTMPurgeConfig = field(default_factory=LTMPurgeConfig)


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


@dataclass(frozen=True)
class MemoryConfig:
    """记忆系统的运行时配置。"""

    memory_extractor_temperature: float = 0.3
    user_profile_cache_ttl: int = 1800
    stm: STMConfig = field(default_factory=STMConfig)
    ltm: LTMConfig = field(default_factory=LTMConfig)
    ltm_memory_types: dict[str, str] = field(
        default_factory=lambda: dict(LTM_MEMORY_TYPES)
    )
    sensitive_patterns: tuple[str, ...] = field(
        default_factory=lambda: SENSITIVE_PATTERNS
    )


# ====================================================================
# RAG 查询改写（仅书面化；不做 HYDE / 退步）
# ====================================================================

@dataclass(frozen=True)
class RagRewriteConfig:
    """RAG 查询书面化改写配置。

    仅作用于文档检索支路；图谱检索不使用。
    """

    # 是否在进入 HybridSearcher 前做书面化改写
    formalize_enabled: bool = True
    # LLM 超时（秒）；超时或失败则回退原问句
    timeout_seconds: float = 3.0
    # 改写结果最大字符数，防止异常长输出
    max_chars: int = 256


# ====================================================================
# 聚合配置
# ====================================================================

@dataclass(frozen=True)
class AppConfig:
    """应用级统一配置。

    所有硬编码常量收敛到此结构，不再分散在各模块中。
    """

    react: ReactConfig = field(default_factory=ReactConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    task_queue: TaskQueueConfig = field(default_factory=TaskQueueConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    rag_rewrite: RagRewriteConfig = field(default_factory=RagRewriteConfig)


app_config = AppConfig()

__all__ = [
    "AppConfig",
    "LTMConfig",
    "LTMDeduplicationConfig",
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
