"""
记忆模块数据模型定义。

STM = Short-Term Memory，短期记忆。
LTM = Long-Term Memory，长期记忆。

本模块定义记忆模块使用的 Pydantic 模型。
"""

from typing import Any, Dict, List, Literal, Optional

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
    """

    user_goal: str = Field(default="", description="用户当前主要诉求")

    confirmed_facts: List[str] = Field(
        default_factory=list,
        description="已经确认的信息"
    )

    tried_solutions: List[str] = Field(
        default_factory=list,
        description="已经尝试过的方案"
    )

    rejected_solutions: List[str] = Field(
        default_factory=list,
        description="用户拒绝过的方案"
    )

    current_state: str = Field(default="", description="当前问题状态")

    next_action: str = Field(default="", description="下一步建议动作")


class LongTermMemory(BaseModel):
    """
    长期记忆记录。

    LTM = Long-Term Memory，长期记忆。
    """

    memory_id: str = Field(..., description="长期记忆唯一 ID")

    tenant_id: str = Field(..., description="租户 ID")

    user_id: str = Field(..., description="用户 ID")

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
    """

    memory_type: Literal[
        "user_profile",
        "issue_history",
        "solution_note"
    ]

    content: str

    reason: Optional[str] = None


class AgentMemoryState(BaseModel):
    session_summary: Optional[SessionSummary] = None
    recent_messages: List[MessageRecord] = Field(default_factory=list)
    long_term_memories: List[MemorySearchResult] = Field(default_factory=list)
    user_profile: Dict[str, Any] = Field(default_factory=dict)  # v3.2: MySQL 画像