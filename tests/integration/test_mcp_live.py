"""Live check against the real act-mcp server (read-only: lists tools)."""

import socket
from urllib.parse import urlparse

import pytest

from act_agent.auth.rbac import normalize_tool_name
from act_agent.mcp.client import McpClientManager
from act_agent.mcp.config import load_config


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def test_lists_real_mcp_tools(settings):
    servers = load_config(settings.resolved_mcp_config)
    if not servers:
        pytest.skip("no mcp config")
    if not all(_reachable(cfg.get("url", "")) for cfg in servers.values()):
        pytest.skip("act-mcp not reachable")

    import asyncio

    async def _run():
        manager = McpClientManager(servers, settings)
        try:
            return await manager.health()
        finally:
            await manager.close()

    health = asyncio.run(_run())
    assert health["status"] == "ok", health
    names = {normalize_tool_name(n) for n in health["tools"]}
    assert "create_big_activity_with_products" in names
    assert "list_big_activities" in names
