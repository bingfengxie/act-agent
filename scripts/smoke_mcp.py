"""Verify the configured MCP server is reachable and list its tools.

Usage:
    uv run python scripts/smoke_mcp.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from act_agent.mcp.client import McpClientManager  # noqa: E402
from act_agent.mcp.config import load_config  # noqa: E402
from act_agent.settings import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    servers = load_config(settings.resolved_mcp_config)
    print(f"config: {settings.resolved_mcp_config}")
    print(f"servers: {servers}")
    manager = McpClientManager(servers, settings)
    health = await manager.health()
    print(f"status: {health['status']}")
    for name in health.get("tools", []):
        print(f"  - {name}")
    if health.get("error"):
        print(f"error: {health['error']}")
        sys.exit(1)
    await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
