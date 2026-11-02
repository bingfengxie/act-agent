from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from act_agent.api.schemas import UserCreateRequest
from act_agent.auth.deps import require_scope
from act_agent.auth.passwords import hash_password
from act_agent.auth.rbac import ROLE_SCOPES, SCOPE_ADMIN, SCOPE_AUDIT, Principal
from act_agent.storage.db import get_db
from act_agent.storage.repository import create_user, list_audit

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/audit")
async def audit_log(
    session_id: str | None = None,
    action: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(require_scope(SCOPE_AUDIT)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    entries = await list_audit(
        db,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        action=action,
        event_type=event_type,
        limit=min(max(limit, 1), 500),
    )
    return {
        "tenant_id": principal.tenant_id,
        "count": len(entries),
        "items": [
            {
                "id": e.id,
                "session_id": e.session_id,
                "thread_id": e.thread_id,
                "user_id": e.user_id,
                "event_type": e.event_type,
                "tool_name": e.tool_name,
                "action": e.action,
                "status": e.status,
                "args": e.args_json,
                "args_hash": e.args_hash,
                "content": e.content,
                "error": e.error,
                "duration_ms": e.duration_ms,
                "request_id": e.request_id,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
    }


@router.post("/users", status_code=201)
async def add_user(
    body: UserCreateRequest,
    principal: Principal = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    unknown = [r for r in body.roles if r not in ROLE_SCOPES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知角色: {unknown}")
    user = await create_user(
        db,
        tenant_id=principal.tenant_id,
        username=body.username,
        password_hash=hash_password(body.password),
        roles=body.roles,
    )
    return {"id": user.id, "tenant_id": user.tenant_id, "username": user.username,
            "roles": user.roles}
