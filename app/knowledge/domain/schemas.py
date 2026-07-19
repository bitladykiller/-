"""记忆模块共享数据模型。

这个模块负责：
- 定义 STM（Short-Term Memory，短期记忆）和
  LTM（Long-Term Memory，长期记忆）共享的 Pydantic / TypedDict 结构
- 约束记忆读写层、抽取层、上下文组装层之间的字段边界

这个模块不负责：
- 记忆存储实现
- 配置管理
- Prompt 组装
"""
from __future__ import annotations

from typing import Literal

from app.user.domain.schemas import UserProfileData
from pydantic import BaseModel, Field


class MessageRecord(BaseModel):
    """
    短期记忆中的单条消息记录。

    STM = Short-Term Memory，短期记忆。
    这里不保存 token_count、intent、entities、tool_calls，
    只保存构造上下文最必要的信息，降低实现复杂度。
    """

    message_id: str = Field(..., description="消息唯一 ID")

    role: Literal["user", "assistant", "tool", "system"] = Field(
        ...,
        description="消息角色"
    )

    content: str = Field(..., description="消息正文")

    created_at: int = Field(..., description="消息创建时间戳，单位秒")

    turn_index: int = Field(..., description="当前会话中的轮次")


class SessionMeta(BaseModel):
    """
    短期会话元信息。

    只保存轮次和更新时间，不做 token 统计。
    """

    total_turns: int = Field(default=0, description="当前 session 总轮次")

    last_updated_at: int = Field(default=0, description="最近更新时间戳")

    last_compressed_turn: int = Field(
        default=0,
        description="最近一次压缩发生在第几轮"
    )


class SessionSummary(BaseModel):
    """
    压缩后的短期会话摘要。

    不预设对话领域（不假设对话是"问题→方案"模式）。
    LLM 自由生成摘要文本，本层只存内容和元信息。
    """

    content: str = Field(default="", description="LLM 生成的压缩摘要，自由格式文本")

    compressed_at: int = Field(default=0, description="压缩时间戳（秒）")

    compressed_round: int = Field(default=0, description="压缩时对应的对话轮次")


class LongTermMemory(BaseModel):
    """
    长期记忆记录。

    LTM = Long-Term Memory，长期记忆。
    """

    memory_id: str = Field(..., description="长期记忆唯一 ID")

    tenant_id: str = Field(..., description="租户 ID")

    user_id: str = Field(..., description="用户 ID")

    session_id: str = Field(
        default="",
        description="关联会话 ID；删除会话时可按 session 清理 LTM",
    )

    memory_type: Literal[
        "issue_history",
        "solution_note"
    ] = Field(..., description="长期记忆类型")

    content: str = Field(..., description="长期记忆内容")

    created_at: int = Field(default=0, description="创建时间戳")

    updated_at: int = Field(default=0, description="更新时间戳")

    last_hit_at: int = Field(default=0, description="最近一次命中时间戳")

    hit_count: int = Field(default=0, description="命中次数")

    is_deleted: bool = Field(default=False, description="是否软删除")


class MemorySearchResult(BaseModel):
    """
    长期记忆检索结果。
    """

    memory: LongTermMemory

    score: float = Field(default=0.0, description="相似度分数")


class MemoryExtractorResult(BaseModel):
    """
    长期记忆抽取结果。

    这里只描述会写入 Milvus 的语义记忆候选项。
    结构化用户画像走独立画像链路，因此不会作为语义记忆类型返回。
    """

    memory_type: Literal[
        "issue_history",
        "solution_note"
    ]

    content: str

    reason: str | None = None


class AgentMemoryState(BaseModel):
    """一次请求里复用的记忆快照。"""

    session_summary: SessionSummary | None = None
    recent_messages: list[MessageRecord] = Field(default_factory=list)
    long_term_memories: list[MemorySearchResult] = Field(default_factory=list)
    user_profile: UserProfileData = Field(  # type: ignore[assignment]
        default_factory=dict,
        description="结构化用户画像快照，由画像存储链路提供",
    )
