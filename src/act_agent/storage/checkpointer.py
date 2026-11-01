"""LangGraph checkpointer lifecycle.

Production uses AsyncPostgresSaver (distributed, shared across workers).
Without a DSN we fall back to InMemorySaver so the service is runnable in dev/test.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)


class CheckpointerManager:
    def __init__(self, dsn: str = "") -> None:
        self._dsn = dsn
        self._stack: AsyncExitStack | None = None
        self._saver: Any = None

    @property
    def is_distributed(self) -> bool:
        return bool(self._dsn)

    @property
    def is_started(self) -> bool:
        return self._saver is not None

    @property
    def saver(self):
        if self._saver is None:
            raise RuntimeError("checkpointer not started")
        return self._saver

    async def start(self) -> None:
        if not self._dsn:
            self._saver = InMemorySaver()
            logger.warning("checkpoint DSN not set - using InMemorySaver (dev only)")
            return

        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        self._stack = AsyncExitStack()
        saver = await self._stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(self._dsn)
        )
        await saver.setup()
        self._saver = saver
        logger.info("AsyncPostgresSaver ready")

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._saver = None
