"""用户知识文档元信息（MySQL）。

Milvus 存 chunk 向量；本表存：
- 稳定 doc_id（与 Milvus chunk.doc_id 对齐）
- 展示名 / 原始文件名
- 版本、状态、content_hash、路径
供前端列表与「更新文档」绑定 doc_id。
"""

from __future__ import annotations

from datetime import datetime

from app.shared.core.database import Base
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column


class UserDocument(Base):
    """用户上传的 RAG 文档主表。

    多租户约束：
    - doc_id 在**租户内**唯一（两个租户可各自拥有 policy.pdf 的 doc_id），
      全局唯一由 UUID 生成器保证，数据库只保证租户内唯一。
    - 查询必须带 tenant_id，否则即为跨租户泄漏。
    """

    __tablename__ = "user_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_id", name="uk_doc_id"),
        Index("idx_user_documents_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="default",
        server_default="default",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 与 Milvus 中 chunk.doc_id 一致，租户内唯一
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 前端展示名（默认同原始文件名，可后续改名）
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # pending | indexing | ready | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_task_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["UserDocument"]
