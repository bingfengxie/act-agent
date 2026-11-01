"""Per-thread mutual exclusion.

With several API workers a user can land on a different instance mid-conversation,
so the lock must be shared. Postgres advisory locks are the production backend;
an in-process asyncio lock is the single-node fallback.
"""

from __future__ import annotations

import asyncio
import logging
import zlib
from typing import Protocol

logger = logging.getLogger(__name__)


class ThreadLockManager(Protocol):
    async def acquire(self, thread_id: str, timeout: float) -> bool: ...
    async def release(self, thread_id: str) -> None: ...
    async def close(self) -> None: ...


class MemoryLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lock(self, thread_id: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(thread_id, asyncio.Lock())

    async def acquire(self, thread_id: str, timeout: float) -> bool:
        lock = await self._lock(thread_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def release(self, thread_id: str) -> None:
        lock = self._locks.get(thread_id)
        if lock and lock.locked():
            lock.release()

    async def close(self) -> None:
        self._locks.clear()


class PgLockManager:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None
        self._held: dict[str, object] = {}
        self._guard = asyncio.Lock()

    async def start(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)

    @staticmethod
    def _key(thread_id: str) -> int:
        # 32-bit crc -> signed 64-bit range, stable across processes
        return zlib.crc32(thread_id.encode("utf-8")) - (1 << 31)

    async def acquire(self, thread_id: str, timeout: float) -> bool:
        if self._pool is None:
            return False
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            conn = await self._pool.acquire()
            got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", self._key(thread_id))
            if got:
                async with self._guard:
                    self._held[thread_id] = conn
                return True
            await self._pool.release(conn)
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.2)

    async def release(self, thread_id: str) -> None:
        async with self._guard:
            conn = self._held.pop(thread_id, None)
        if conn is None:
            return
        try:
            await conn.fetchval("SELECT pg_advisory_unlock($1)", self._key(thread_id))
        finally:
            await self._pool.release(conn)

    async def close(self) -> None:
        for thread_id in list(self._held):
            await self.release(thread_id)
        if self._pool is not None:
            await self._pool.close()


class LockManagerHolder:
    """Process-wide singleton, initialised in the app lifespan."""

    def __init__(self) -> None:
        self.manager: ThreadLockManager = MemoryLockManager()

    async def start(self, backend: str, dsn: str) -> None:
        if backend == "pg" and dsn:
            pg = PgLockManager(dsn)
            await pg.start()
            self.manager = pg
            logger.info("thread locks: postgres advisory locks")
        else:
            self.manager = MemoryLockManager()
            logger.warning("thread locks: in-process only (single worker)")

    async def stop(self) -> None:
        await self.manager.close()


lock_holder = LockManagerHolder()
