"""Async engine / session factory and schema bootstrap."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from act_agent.settings import get_settings
from act_agent.storage.models import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite"):
        # sqlite+aiosqlite:///./data/act_agent.db
        raw = url.split("///", 1)[-1]
        path = raw.split("?", 1)[0]
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _ensure_sqlite_dir(settings.database_url)
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            echo=settings.debug,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def _ensure_audit_columns(sync_conn) -> None:
    """``create_all`` 不会给已存在的表补列；补齐 audit_logs 新增的 event_type/content。

    项目无 Alembic，用最小化的 ``ALTER TABLE ADD COLUMN`` 兼容已有 SQLite/Postgres 库。
    多 worker（``--workers 2``）首次启动会并发执行本函数，check-then-ALTER 非原子，
    因此对「列已存在」类错误做容错，避免第二个 worker 撞 duplicate column 而崩。
    """
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    if "audit_logs" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("audit_logs")}
    additions = []
    if "event_type" not in existing:
        additions.append(
            "ALTER TABLE audit_logs ADD COLUMN event_type VARCHAR(32) "
            "NOT NULL DEFAULT 'tool_call'"
        )
    if "content" not in existing:
        additions.append("ALTER TABLE audit_logs ADD COLUMN content TEXT")

    for stmt in additions:
        try:
            sync_conn.execute(text(stmt))
        except Exception as exc:  # noqa: BLE001 - 并发 worker 可能已加列
            logger.warning("audit column migration skipped: %s (%s)", stmt[:48], exc)


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_audit_columns)
    logger.info("database schema ready: %s", get_settings().database_url)


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
