"""FastAPI dependencies for auth. Tenant always comes from the token, never the body."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from act_agent.auth.jwt import decode_access_token, principal_from_claims
from act_agent.auth.rbac import Principal
from act_agent.settings import get_settings

_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(credentials.credentials, get_settings())
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="token 已过期") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="token 无效") from exc
    return principal_from_claims(claims)


def require_scope(scope: str):
    async def _checker(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限 {scope}",
            )
        return principal

    return _checker
