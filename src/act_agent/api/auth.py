from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from act_agent.api.schemas import LoginRequest, TokenResponse
from act_agent.auth.deps import get_principal
from act_agent.auth.jwt import create_access_token
from act_agent.auth.passwords import dummy_verify, verify_password
from act_agent.auth.rbac import Principal, scopes_for_roles
from act_agent.settings import get_settings
from act_agent.storage.db import get_db
from act_agent.storage.repository import get_user_by_username

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    settings = get_settings()
    user = await get_user_by_username(db, body.tenant, body.username)
    if user is None:
        dummy_verify(body.password)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.disabled:
        raise HTTPException(status_code=403, detail="账号已禁用")
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        roles=list(user.roles or []),
        settings=settings,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user={
            "id": user.id,
            "tenant_id": user.tenant_id,
            "username": user.username,
            "roles": user.roles,
            "scopes": sorted(scopes_for_roles(list(user.roles or []))),
        },
    )


@router.get("/me")
async def me(principal: Principal = Depends(get_principal)) -> dict:
    return {
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "username": principal.username,
        "roles": list(principal.roles),
        "scopes": sorted(principal.scopes),
    }
