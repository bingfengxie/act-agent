"""Translate the Claude-style mcp.json into langchain-mcp-adapters connections.

Input (what users already have):
    {"mcpServers": {"act-mcp": {"type": "http", "url": "http://localhost:8090/mcp"}}}

Output (adapters format):
    {"act-mcp": {"transport": "streamable_http", "url": "http://localhost:8090/mcp"}}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TYPE_TO_TRANSPORT = {
    "http": "streamable_http",
    "streamable_http": "streamable_http",
    "streamable-http": "streamable_http",
    "streamablehttp": "streamable_http",
    "sse": "sse",
    "stdio": "stdio",
}


def normalize_server(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"mcp server {name}: config must be an object")

    # Already in adapters format?
    if "transport" in raw:
        transport = str(raw["transport"]).lower()
        if transport not in _TYPE_TO_TRANSPORT.values():
            raise ValueError(f"mcp server {name}: unsupported transport {transport}")
        return {**raw, "transport": transport}

    kind = str(raw.get("type", raw.get("transport", "http"))).lower()
    transport = _TYPE_TO_TRANSPORT.get(kind)
    if transport is None:
        raise ValueError(f"mcp server {name}: unsupported type {kind}")

    if transport in {"streamable_http", "sse"}:
        if not raw.get("url"):
            raise ValueError(f"mcp server {name}: url is required")
        out: dict[str, Any] = {"transport": transport, "url": raw["url"]}
        if raw.get("headers"):
            out["headers"] = raw["headers"]
        return out

    if not raw.get("command"):
        raise ValueError(f"mcp server {name}: command is required for stdio")
    out = {"transport": "stdio", "command": raw["command"], "args": raw.get("args", [])}
    if raw.get("env"):
        out["env"] = raw["env"]
    return out


def parse_config(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    servers = data.get("mcpServers") or data.get("mcp_servers") or data
    if not isinstance(servers, dict):
        raise ValueError("mcp config: mcpServers must be an object")
    return {name: normalize_server(name, cfg) for name, cfg in servers.items()}


def load_config(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        logger.warning("mcp config not found: %s", path)
        return {}
    with path.open(encoding="utf-8") as fh:
        return parse_config(json.load(fh))


def merge(*configs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Later configs override earlier ones by server name."""
    out: dict[str, dict[str, Any]] = {}
    for cfg in configs:
        out.update(cfg)
    return out
