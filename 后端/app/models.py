"""试用身份、账号和方案生成相关的数据模型。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.db_types import (
    EMPTY_JSON_ARRAY,
    EMPTY_JSON_OBJECT,
    EMPTY_UUID_LIST,
    JSON_DOCUMENT,
    UTC_NOW,
    UtcDateTime,
    UuidList,
)


_ACTIVE_ROW = text("deleted_at IS NULL")


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class TrialUser(Base):
    """BE-110 试用者：Cookie 只存哈希，30 天可写 + 7 天只读。"""

    __tablename__ = "trial_user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)   # 可写截止
    purge_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)     # 只读截止 / 之后 403

    __table_args__ = (Index("ix_trial_user_expires_at", "expires_at"),)


class Material(Base):
    """BE-120 ~ BE-122 知识库材料。"""

    __tablename__ = "material"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trial_user.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    topic: Mapped[str] = mapped_column(String(40), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)   # web | courseware | manual
    source_url: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(String(200))
    source_page_start: Mapped[int | None] = mapped_column(Integer)
    source_page_end: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="citable")  # citable | link_only | unusable
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)

    __table_args__ = (
        Index("ix_material_owner_topic", "owner_id", "topic", postgresql_where=_ACTIVE_ROW, sqlite_where=_ACTIVE_ROW),
        Index("ix_material_owner_updated", "owner_id", "updated_at"),
    )


class Conversation(Base):
    """BE-130 ~ BE-134 本次对话。"""

    __tablename__ = "conversation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trial_user.id", ondelete="CASCADE"), nullable=False
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False)  # review | travel | activity
    material_ids: Mapped[list[uuid.UUID]] = mapped_column(UuidList, nullable=False, server_default=EMPTY_UUID_LIST)
    conditions: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False, server_default=EMPTY_JSON_OBJECT)
    retained: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False, server_default=EMPTY_JSON_OBJECT)
    turns: Mapped[list] = mapped_column(JSON_DOCUMENT, nullable=False, server_default=EMPTY_JSON_ARRAY)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="retrieved")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)

    __table_args__ = (Index("ix_conversation_owner_created", "owner_id", "created_at"),)


class Generation(Base):
    """BE-150 一次生成请求（异步、可取消、可废弃）。"""

    __tablename__ = "generation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False
    )
    conversation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    # pending | running | succeeded | failed | cancelled | superseded | timeout | blocked
    request: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False, server_default=EMPTY_JSON_OBJECT)
    result: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    validation: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    block_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(80))
    # 单次用量与费用：{model, elapsed_ms, prompt_tokens, completion_tokens, total_tokens, cost_cny}
    usage: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    # 本次改动影响：与同一对话上一次成功生成相比，时长/环节/物料怎么变了
    changes: Mapped[list | None] = mapped_column(JSON_DOCUMENT)
    # 条件冲突时给的三个固定选项：[{action, label, effect, send, ...}]
    options: Mapped[list | None] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (Index("ix_generation_conv_created", "conversation_id", "created_at"),)


class Citation(Base):
    """BE-160 出处快照：材料删除后仍可展开。"""

    __tablename__ = "citation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("generation.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(String(200))
    source_page_start: Mapped[int | None] = mapped_column(Integer)
    source_page_end: Mapped[int | None] = mapped_column(Integer)
    material_title: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)

    __table_args__ = (Index("ix_citation_generation", "generation_id", "ordinal"),)


class Plan(Base):
    """BE-140 / BE-141 已保存方案：同一对话只保留一条有效记录（UPSERT）。"""

    __tablename__ = "plan"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_private_note: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    confirmed_conditions: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False, server_default=EMPTY_JSON_OBJECT)
    citation_ids: Mapped[list[uuid.UUID]] = mapped_column(UuidList, nullable=False, server_default=EMPTY_UUID_LIST)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)

    __table_args__ = (
        Index(
            "uq_plan_owner_conversation_active",
            "owner_id",
            "conversation_id",
            unique=True,
            postgresql_where=_ACTIVE_ROW,
            sqlite_where=_ACTIVE_ROW,
        ),
        Index("ix_plan_owner_updated", "owner_id", "updated_at"),
    )


class GenerationLog(Base):
    """BE-165 生成过程留痕：拦截、修正、降级、异常都落一行。"""

    __tablename__ = "generation_log"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    generation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str | None] = mapped_column(String(48))
    detail: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)

    __table_args__ = (Index("ix_generation_log_gen", "generation_id", "created_at"),)


class Account(Base):
    """账号：一个账号对应一个数据身份（trial_user），多设备登录共用同一份数据。"""

    __tablename__ = "account"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    trial_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trial_user.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)


class AuthToken(Base):
    """登录会话：一个账号可以有多个令牌（多设备），令牌只存哈希。"""

    __tablename__ = "auth_token"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    trial_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trial_user.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("account.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=UTC_NOW, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (Index("ix_auth_token_trial_user", "trial_user_id"),)
