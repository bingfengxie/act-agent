import os
from pathlib import Path

os.environ.setdefault(
    "ACT_AGENT_DATABASE_URL", "sqlite+aiosqlite:///./data/test_act_agent.db"
)
os.environ.setdefault("ACT_AGENT_CHECKPOINT_DSN", "")
os.environ.setdefault("ACT_AGENT_LOCK_BACKEND", "memory")
os.environ.setdefault("ACT_AGENT_LLM_API_KEY", "test-key")
os.environ.setdefault(
    "ACT_AGENT_SKILLS_DIR", str(Path(__file__).resolve().parents[2] / "act-skills")
)

import pytest  # noqa: E402

from act_agent.settings import get_settings  # noqa: E402
from act_agent.storage.db import dispose_db, init_db  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(autouse=True)
async def _database():
    """每个测试一份干净库（sqlite 上 create_all 很快，且避免跨用例串数据）。"""
    from sqlalchemy import delete

    from act_agent.storage.db import get_session_factory
    from act_agent.storage.models import AuditLog, SessionRecord, User

    await init_db()
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(delete(AuditLog))
        await db.execute(delete(SessionRecord))
        await db.execute(delete(User))
        await db.commit()
    yield
    await dispose_db()
