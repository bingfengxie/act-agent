from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from act_agent.api import admin, auth, chat, sessions
from act_agent.runtime import runtime
from act_agent.settings import get_settings

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(
    title="act-agent",
    version="0.1.0",
    description="Multi-tenant activity management agent (LangGraph + MCP)",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(admin.router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    settings = get_settings()
    mcp_status = await runtime.mcp.health() if runtime.mcp is not None else {"status": "starting"}
    return {
        "status": "ok" if mcp_status.get("status") != "error" else "degraded",
        "checkpoint": "postgres" if runtime.checkpointer.is_distributed else "memory",
        "skills": [s.name for s in runtime.registry.all()],
        "mcp": mcp_status,
        "llm": {"provider": settings.llm_provider, "model": settings.llm_model},
    }
