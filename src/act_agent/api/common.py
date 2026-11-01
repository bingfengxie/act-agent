from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from act_agent.auth.rbac import Principal
from act_agent.storage.models import SessionRecord
from act_agent.storage.repository import get_session


def thread_id_for(principal: Principal, session_id: str) -> str:
    return f"{principal.tenant_id}:{principal.user_id}:{session_id}"


async def owned_session(
    db: AsyncSession, session_id: str, principal: Principal
) -> SessionRecord:
    record = await get_session(db, session_id, principal.tenant_id)
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if record.user_id != principal.user_id and "admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return record
