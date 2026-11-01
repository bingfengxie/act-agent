"""Thin data-access helpers. Every query is tenant-scoped at the call site."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from act_agent.storage.models import AuditLog, SessionRecord, Tenant, User


def args_digest(args: dict) -> str:
    payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def ensure_tenant(db: AsyncSession, tenant_id: str, name: str | None = None) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        tenant = Tenant(id=tenant_id, name=name or tenant_id, config={})
        db.add(tenant)
        await db.commit()
    return tenant


async def get_user_by_username(
    db: AsyncSession, tenant_id: str, username: str
) -> User | None:
    stmt = select(User).where(User.tenant_id == tenant_id, User.username == username)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    *,
    tenant_id: str,
    username: str,
    password_hash: str,
    roles: list[str],
    user_id: str | None = None,
) -> User:
    user = User(
        tenant_id=tenant_id,
        username=username,
        password_hash=password_hash,
        roles=roles,
    )
    if user_id:
        user.id = user_id
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_session(
    db: AsyncSession,
    *,
    session_id: str,
    tenant_id: str,
    user_id: str,
    thread_id: str,
    title: str = "新会话",
) -> SessionRecord:
    record = SessionRecord(
        id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        title=title,
        status="idle",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_session(
    db: AsyncSession, session_id: str, tenant_id: str
) -> SessionRecord | None:
    stmt = select(SessionRecord).where(
        SessionRecord.id == session_id, SessionRecord.tenant_id == tenant_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_sessions(
    db: AsyncSession, tenant_id: str, user_id: str | None = None, limit: int = 50
) -> Sequence[SessionRecord]:
    stmt = select(SessionRecord).where(SessionRecord.tenant_id == tenant_id)
    if user_id:
        stmt = stmt.where(SessionRecord.user_id == user_id)
    stmt = stmt.order_by(SessionRecord.last_active_at.desc()).limit(limit)
    return (await db.execute(stmt)).scalars().all()


async def update_session(
    db: AsyncSession,
    record: SessionRecord,
    *,
    status: str | None = None,
    skill: str | None = None,
    title: str | None = None,
    token_usage: int | None = None,
) -> None:
    if status is not None:
        record.status = status
    if skill is not None:
        record.skill = skill
    if title is not None:
        record.title = title
    if token_usage is not None:
        record.token_usage = token_usage
    record.last_active_at = datetime.now(UTC)
    await db.commit()


async def delete_session(db: AsyncSession, record: SessionRecord) -> None:
    await db.delete(record)
    await db.commit()


async def count_active_sessions(db: AsyncSession, tenant_id: str, user_id: str) -> int:
    stmt = select(func.count()).select_from(SessionRecord).where(
        SessionRecord.tenant_id == tenant_id, SessionRecord.user_id == user_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def write_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str | None,
    thread_id: str | None,
    tool_name: str,
    action: str,
    args: dict,
    status: str,
    error: str | None = None,
    request_id: str | None = None,
    duration_ms: int = 0,
) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        thread_id=thread_id,
        tool_name=tool_name,
        action=action,
        args_json=args,
        args_hash=args_digest(args),
        status=status,
        error=error,
        request_id=request_id,
        duration_ms=duration_ms,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    session_id: str | None = None,
    action: str | None = None,
    limit: int = 100,
) -> Sequence[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if session_id:
        stmt = stmt.where(AuditLog.session_id == session_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
    return (await db.execute(stmt)).scalars().all()


async def get_tenant(db: AsyncSession, tenant_id: str) -> Tenant | None:
    return await db.get(Tenant, tenant_id)
