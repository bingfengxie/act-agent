from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from act_agent.api.common import owned_session, thread_id_for
from act_agent.api.schemas import SessionCreateRequest, SessionOut
from act_agent.auth.deps import require_scope
from act_agent.auth.rbac import SCOPE_READ, Principal
from act_agent.runtime import runtime
from act_agent.settings import get_settings
from act_agent.storage.db import get_db
from act_agent.storage.models import new_id
from act_agent.storage.repository import (
    count_active_sessions,
    create_session,
    delete_session,
    list_sessions,
)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut, status_code=201)
async def create(
    body: SessionCreateRequest,
    principal: Principal = Depends(require_scope(SCOPE_READ)),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    settings = get_settings()
    active = await count_active_sessions(db, principal.tenant_id, principal.user_id)
    if active >= settings.max_concurrent_sessions_per_tenant:
        raise HTTPException(status_code=429, detail="会话数量已达上限，请先清理旧会话")
    session_id = new_id()
    record = await create_session(
        db,
        session_id=session_id,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        thread_id=thread_id_for(principal, session_id),
        title=body.title or "新会话",
    )
    return SessionOut.model_validate(record)


@router.get("", response_model=list[SessionOut])
async def list_mine(
    principal: Principal = Depends(require_scope(SCOPE_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[SessionOut]:
    records = await list_sessions(db, principal.tenant_id, principal.user_id)
    return [SessionOut.model_validate(r) for r in records]


@router.get("/{session_id}", response_model=SessionOut)
async def get_one(
    session_id: str,
    principal: Principal = Depends(require_scope(SCOPE_READ)),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    record = await owned_session(db, session_id, principal)
    return SessionOut.model_validate(record)


@router.get("/{session_id}/state")
async def get_state(
    session_id: str,
    principal: Principal = Depends(require_scope(SCOPE_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    record = await owned_session(db, session_id, principal)
    state = await runtime.service.get_state(thread_id=record.thread_id, principal=principal)
    return {"session": SessionOut.model_validate(record).model_dump(), **state}


@router.delete("/{session_id}", status_code=204)
async def remove(
    session_id: str,
    principal: Principal = Depends(require_scope(SCOPE_READ)),
    db: AsyncSession = Depends(get_db),
) -> None:
    record = await owned_session(db, session_id, principal)
    if runtime.checkpointer.is_started:
        saver = runtime.checkpointer.saver
        delete_thread = getattr(saver, "adelete_thread", None)
        if delete_thread is not None:
            try:
                await delete_thread(record.thread_id)
            except Exception:  # noqa: BLE001 - checkpoint cleanup is best effort
                pass
    await delete_session(db, record)
