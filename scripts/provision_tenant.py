"""Create a tenant and its first admin user.

Usage:
    uv run python scripts/provision_tenant.py --tenant acme --username root --password secret123
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from act_agent.auth.passwords import hash_password  # noqa: E402
from act_agent.storage.db import dispose_db, get_session_factory, init_db  # noqa: E402
from act_agent.storage.models import User  # noqa: E402
from act_agent.storage.repository import create_user, ensure_tenant  # noqa: E402


async def main(args: argparse.Namespace) -> None:
    await init_db()
    async with get_session_factory()() as db:
        await ensure_tenant(db, args.tenant, args.tenant)
        existing = (
            await db.execute(select(User).where(User.tenant_id == args.tenant))
        ).scalars().first()
        if existing is not None and not args.force:
            print(f"tenant {args.tenant} already has users; use --force to add anyway")
            return
        user = await create_user(
            db,
            tenant_id=args.tenant,
            username=args.username,
            password_hash=hash_password(args.password),
            roles=["admin"],
        )
        print(f"created tenant={user.tenant_id} user={user.username} roles={user.roles}")
    await dispose_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--force", action="store_true")
    asyncio.run(main(parser.parse_args()))
