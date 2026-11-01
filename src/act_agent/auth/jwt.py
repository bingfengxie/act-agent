from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from act_agent.auth.rbac import Principal, scopes_for_roles
from act_agent.settings import Settings


def create_access_token(
    *, user_id: str, tenant_id: str, username: str, roles: list[str], settings: Settings
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "tenant": tenant_id,
        "username": username,
        "roles": roles,
        "scopes": sorted(scopes_for_roles(roles)),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
        "iss": settings.app_name,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.app_name,
    )


def principal_from_claims(claims: dict) -> Principal:
    return Principal(
        user_id=claims["sub"],
        tenant_id=claims["tenant"],
        username=claims.get("username", ""),
        roles=tuple(claims.get("roles", [])),
        scopes=frozenset(claims.get("scopes", [])),
    )
