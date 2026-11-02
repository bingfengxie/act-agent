"""Async engine / session factory and schema bootstrap."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from act_agent.settings import get_settings
from act_agent.storage.models import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# 多 worker 串行化 schema bootstrap 的事务级 advisory lock key（仅需进程间一致）
_SCHEMA_ADVISORY_LOCK_KEY = 0x6163745F6462  # "act_db"


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


def _run_ddl(sync_conn, stmt: str) -> None:
    """在 SAVEPOINT 内执行一条 DDL：单条失败不影响后续语句。

    Postgres 事务一旦出错就进入 aborted 状态，后续语句全部被拒（即日志里的
    ``current transaction is aborted``）。savepoint 把失败隔离在单条语句内，
    既保证补列/补索引继续执行，也不会污染 ``init_db`` 的外层事务。
    """
    try:
        with sync_conn.begin_nested():
            sync_conn.execute(text(stmt))
    except Exception as exc:  # noqa: BLE001 - DDL 幂等，失败仅告警
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        logger.warning("schema DDL skipped: %s (%s: %s)", stmt[:64], type(exc).__name__, first_line)


def _ensure_audit_columns(sync_conn) -> None:
    """``create_all`` 不会给已存在的表补列/建索引；补齐 audit_logs 的列与索引。

    项目无 Alembic，用最小化的 ``ALTER TABLE`` 兼容已有库。多 worker（``--workers 2``）
    会并发执行本函数，因此：
    - Postgres 直接用 ``ADD COLUMN IF NOT EXISTS``（加 ACCESS EXCLUSIVE 锁后复查，
      第二个 worker 自动 no-op）；
    - SQLite 不支持 ``ADD COLUMN IF NOT EXISTS``，退回 check-then-ALTER，由 savepoint 兜底。
    """
    from sqlalchemy import inspect

    insp = inspect(sync_conn)
    if "audit_logs" not in insp.get_table_names():
        return

    if sync_conn.dialect.name == "postgresql":
        additions = [
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS event_type "
            "VARCHAR(32) NOT NULL DEFAULT 'tool_call'",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS content TEXT",
        ]
    else:
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
        _run_ddl(sync_conn, stmt)

    # create_all 只为「新建表」建索引；老库补列后必须单独补索引，
    # 否则按 event_type 过滤的审计查询会走全表扫描。
    for index_stmt in (
        "CREATE INDEX IF NOT EXISTS ix_audit_tenant_created "
        "ON audit_logs (tenant_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_audit_thread ON audit_logs (thread_id)",
        "CREATE INDEX IF NOT EXISTS ix_audit_event_type ON audit_logs (event_type)",
    ):
        _run_ddl(sync_conn, index_stmt)


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        # 多 worker 并发 DDL 会互相撞车（duplicate column / duplicate table / duplicate index）。
        # Postgres 用事务级 advisory lock 串行化整个 bootstrap，锁在 commit 时自动释放，
        # 后到的 worker 会直接看到已建好的 schema。
        if conn.dialect.name == "postgresql":
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _SCHEMA_ADVISORY_LOCK_KEY},
            )
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
