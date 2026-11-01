"""Per-tenant MCP client cache built on langchain-mcp-adapters."""

from __future__ import annotations

import asyncio
import json
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from act_agent.settings import Settings

logger = logging.getLogger(__name__)


class McpUnavailable(RuntimeError):
    pass


class McpClientManager:
    def __init__(self, base_servers: dict[str, dict], settings: Settings) -> None:
        self.base_servers = base_servers
        self.settings = settings
        self._cache: dict[str, tuple[list, object]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(servers: dict[str, dict]) -> str:
        return json.dumps(servers, sort_keys=True, ensure_ascii=False, default=str)

    def servers_for(self, tenant_servers: dict[str, dict] | None) -> dict[str, dict]:
        merged = dict(self.base_servers)
        if tenant_servers:
            merged.update(tenant_servers)
        return merged

    async def get_tools(self, tenant_servers: dict[str, dict] | None = None) -> list:
        servers = self.servers_for(tenant_servers)
        if not servers:
            return []
        key = self._key(servers)
        async with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached[0]

        try:
            client = MultiServerMCPClient(servers)
            tools = await asyncio.wait_for(
                client.get_tools(), timeout=self.settings.mcp_connect_timeout_seconds * 4
            )
        except Exception as exc:  # noqa: BLE001
            raise McpUnavailable(f"MCP 工具加载失败: {exc}") from exc

        async with self._lock:
            self._cache[key] = (tools, client)
        logger.info(
            "loaded %d MCP tools from %s: %s",
            len(tools), ", ".join(servers), ", ".join(t.name for t in tools),
        )
        return tools

    async def health(self, tenant_servers: dict[str, dict] | None = None) -> dict:
        servers = self.servers_for(tenant_servers)
        try:
            tools = await self.get_tools(tenant_servers)
            return {
                "status": "ok",
                "servers": list(servers),
                "tool_count": len(tools),
                "tools": [t.name for t in tools],
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "servers": list(servers), "error": str(exc)}

    async def close(self) -> None:
        for _tools, client in self._cache.values():
            for attr in ("close", "aclose"):
                closer = getattr(client, attr, None)
                if closer:
                    try:
                        result = closer()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:  # noqa: BLE001
                        pass
                    break
        self._cache.clear()
