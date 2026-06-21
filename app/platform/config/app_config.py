"""统一应用配置 — 聚合基础设施、业务和运行时行为配置。

职责：
- 作为所有配置项的单一入口
- 将代码中的硬编码常量集中管理
- 提供分层的配置访问接口

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
    """最大重试轮数。"""

    recursion_limit: int = 11
    """单次 ReAct 子图的最大 agent/tools 步数。"""

    transcript_window: int = 20
    """传递给答案充分性检查的最近消息条数。"""

    progress_message: str = "正在综合分析..."
    """ReAct 执行中返回给用户的进度提示。"""

    fallback_answer: str = "亲～这个问题回答不了哦～"
    """所有重试用尽后的兜底回复。"""

    retry_prompt: str = (
        "上一次候选答案仍然不充分，请继续按标准 ReAct 检索并补足关键事实。"
    )
    """重试时注入的提示。"""

    step_exhausted_marker: str = "need more steps"
    """判断 ReAct 步数耗尽的标记字符串。"""

    step_exhausted_reason: str = "单次 ReAct 内部步数耗尽，仍未得到足够答案。"
    """步数耗尽时的不足原因说明。"""

    default_insufficiency_reason: str = "答案信息不足。"
    """默认的不足原因。"""

    initial_reason: str = "初始状态：尚未完成充分回答。"
    """初始不足原因。"""


# ====================================================================
# 文档上传配置
# ====================================================================

@dataclass(frozen=True)
class UploadConfig:
    """文档上传的运行时配置。"""

    max_upload_size_mb: int = 50
    """最大上传文件大小（MB）。"""

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
    """Redis 中任务状态的 key 前缀。"""

    task_ttl_seconds: int = 3600 * 24
    """任务状态在 Redis 中的保留时间（24 小时）。"""


# ====================================================================
# 记忆系统配置
# ====================================================================

@dataclass(frozen=True)
class MemoryConfig:
    """记忆系统的运行时配置。"""

    memory_extractor_temperature: float = 0.3
    """记忆抽取 LLM 的温度参数。"""

    user_profile_cache_ttl: int = 1800
    """用户画像 Redis 缓存过期时间（秒）。"""


# ====================================================================
# 检索器配置
# ====================================================================

@dataclass(frozen=True)
class RetrieverConfig:
    """检索器的运行时配置。"""

    hybrid_search_limit_multiplier: int = 2
    """混合检索时的候选数量倍增系数。"""

    compress_fetch_limit: int = 100
    """STM 压缩时一次拉取的消息上限。"""


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
    retriever: RetrieverConfig = field(default_factory=RetrieverConfig)


# 模块级单例（不可变，无需锁）
app_config = AppConfig()

__all__ = [
    "AppConfig",
    "MemoryConfig",
    "ReactConfig",
    "RetrieverConfig",
    "TaskQueueConfig",
    "UploadConfig",
    "app_config",
]
