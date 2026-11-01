"""Seed the default tenant and demo accounts on first start."""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from act_agent.auth.passwords import hash_password
from act_agent.settings import get_settings
from act_agent.storage.db import get_session_factory
from act_agent.storage.models import User
from act_agent.storage.repository import create_user, ensure_tenant

logger = logging.getLogger(__name__)

DEMO_USERS = [
    ("admin", "admin123", ["admin"]),
    ("operator", "operator123", ["operator"]),
    ("viewer", "viewer123", ["viewer"]),
]


async def bootstrap_defaults() -> None:
    settings = get_settings()
    async with get_session_factory()() as db:
        await ensure_tenant(db, settings.bootstrap_tenant, settings.bootstrap_tenant)
        total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        if total:
            return
        await create_user(
            db,
            tenant_id=settings.bootstrap_tenant,
            username=settings.bootstrap_admin_username,
            password_hash=hash_password(settings.bootstrap_admin_password),
            roles=["admin"],
        )
        for username, password, roles in DEMO_USERS[1:]:
            await create_user(
                db,
                tenant_id=settings.bootstrap_tenant,
                username=username,
                password_hash=hash_password(password),
                roles=roles,
            )
        logger.warning(
            "bootstrapped demo users in tenant=%s: %s",
            settings.bootstrap_tenant,
            ", ".join(u[0] for u in DEMO_USERS),
        )
