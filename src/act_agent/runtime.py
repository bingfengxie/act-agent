"""Process-wide runtime: wires settings, storage, skills, MCP, LLM and the graph."""

from __future__ import annotations

import logging

from act_agent.graph.builder import build_graph
from act_agent.llm import build_llm
from act_agent.mcp.client import McpClientManager
from act_agent.mcp.config import load_config, parse_config
from act_agent.service import AgentService
from act_agent.settings import Settings, get_settings
from act_agent.skills.registry import SkillRegistry
from act_agent.storage.checkpointer import CheckpointerManager
from act_agent.storage.db import dispose_db, init_db
from act_agent.storage.locks import lock_holder

logger = logging.getLogger(__name__)


class AppRuntime:
    def __init__(self) -> None:
        self.settings: Settings = get_settings()
        self.registry = SkillRegistry(self.settings.resolved_skills_dir)
        self.checkpointer = CheckpointerManager(self.settings.checkpoint_dsn)
        self.mcp: McpClientManager | None = None
        self.llm = None
        self.graph = None
        self.service: AgentService | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        if self.settings.is_default_jwt_secret:
            logger.warning("using default JWT secret - set ACT_AGENT_JWT_SECRET before production")
        await init_db()
        from act_agent.storage.bootstrap import bootstrap_defaults

        await bootstrap_defaults()

        await self.checkpointer.start()
        await lock_holder.start(self.settings.lock_backend, self.settings.checkpoint_dsn)

        self.registry.discover()
        base_servers = load_config(self.settings.resolved_mcp_config)
        self.mcp = McpClientManager(base_servers, self.settings)
        self.llm = build_llm(self.settings)
        self.graph = build_graph(self.checkpointer.saver)
        self.service = AgentService(
            settings=self.settings,
            registry=self.registry,
            mcp=self.mcp,
            llm=self.llm,
            graph=self.graph,
        )
        self._started = True
        logger.info("act-agent runtime ready")

    async def stop(self) -> None:
        if self.mcp is not None:
            await self.mcp.close()
        await lock_holder.stop()
        await self.checkpointer.stop()
        await dispose_db()
        self._started = False

    def tenant_mcp_servers(self, tenant_config: dict | None) -> dict:
        """Tenant config may add/override MCP servers in either format."""
        if not tenant_config:
            return {}
        raw = tenant_config.get("mcpServers") or tenant_config.get("mcp_servers") or {}
        if not raw:
            return {}
        try:
            return parse_config(
                raw if ("mcpServers" in raw or "mcp_servers" in raw) else {"mcpServers": raw}
            )
        except Exception as exc:  # noqa: BLE001 - bad tenant config must not break routing
            logger.error("invalid tenant mcp config: %s", exc)
            return {}


runtime = AppRuntime()
