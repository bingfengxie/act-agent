"""Streaming chat endpoints: Streamable HTTP（stateless），响应体为 SSE 事件格式。

每个用户消息是一个自包含 POST：turn 结束即关闭连接，不维护跨请求的服务端会话，
LB 可把同 session 的不同消息路由到任意 worker，状态由 Postgres checkpoint + advisory lock 恢复。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from act_agent.api.common import owned_session
from act_agent.api.schemas import MessageRequest, ResumeRequest
from act_agent.auth.deps import require_scope
from act_agent.auth.rbac import SCOPE_READ, Principal
from act_agent.runtime import runtime
from act_agent.settings import get_settings
from act_agent.storage.db import get_db, get_session_factory
from act_agent.storage.locks import lock_holder
from act_agent.storage.models import SessionRecord
from act_agent.storage.repository import get_tenant, update_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/sessions", tags=["chat"])


async def _tenant_config(db: AsyncSession, tenant_id: str) -> dict:
    tenant = await get_tenant(db, tenant_id)
    return dict(tenant.config or {}) if tenant else {}


async def _persist(
    session_id: str,
    status: str,
    *,
    title: str | None = None,
) -> None:
    async with get_session_factory()() as db:
        record = await db.get(SessionRecord, session_id)
        if record is not None:
            await update_session(db, record, status=status, title=title)


def _sse_frame(event: str, data: str) -> str:
    """单条 SSE 帧（Streamable HTTP 的 SSE 响应体）。"""
    return f"event: {event}\ndata: {data}\n\n"


async def _sse(
    *,
    record: SessionRecord,
    principal: Principal,
    tenant_config: dict,
    message: str | None = None,
    resume: str | None = None,
):
    service = runtime.service
    final_status = "idle"
    try:
        await _persist(record.id, "running")
        async for event in service.stream_turn(
            principal=principal,
            session_id=record.id,
            thread_id=record.thread_id,
            tenant_config=tenant_config,
            message=message,
            resume=resume,
        ):
            name = event.pop("event")
            if name == "done":
                final_status = str(event.get("status", "idle"))
            yield _sse_frame(name, json.dumps(event, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - keep the stream well-formed on crash
        logger.exception("stream failed for session %s", record.id)
        final_status = "failed"
        yield _sse_frame("error", json.dumps({"message": "内部错误"}, ensure_ascii=False))
        yield _sse_frame("done", json.dumps({"status": "failed"}, ensure_ascii=False))
    finally:
        title = None
        if message and record.title == "新会话":
            title = message.strip()[:30] or "新会话"
        try:
            await _persist(record.id, final_status, title=title)
        finally:
            await lock_holder.manager.release(record.thread_id)


async def _acquire_or_409(record: SessionRecord) -> None:
    settings = get_settings()
    acquired = await lock_holder.manager.acquire(
        record.thread_id, timeout=settings.session_lock_ttl_seconds
    )
    if not acquired:
        raise HTTPException(status_code=409, detail="该会话正在处理中，请稍后重试")


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageRequest,
    principal: Principal = Depends(require_scope(SCOPE_READ)),
    db: AsyncSession = Depends(get_db),
):
    record = await owned_session(db, session_id, principal)
    tenant_config = await _tenant_config(db, principal.tenant_id)
    await _acquire_or_409(record)
    return StreamingResponse(
        _sse(
            record=record,
            principal=principal,
            tenant_config=tenant_config,
            message=body.message,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/resume")
async def resume(
    session_id: str,
    body: ResumeRequest,
    principal: Principal = Depends(require_scope(SCOPE_READ)),
    db: AsyncSession = Depends(get_db),
):
    record = await owned_session(db, session_id, principal)
    tenant_config = await _tenant_config(db, principal.tenant_id)
    await _acquire_or_409(record)
    return StreamingResponse(
        _sse(
            record=record,
            principal=principal,
            tenant_config=tenant_config,
            resume=body.answer,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
