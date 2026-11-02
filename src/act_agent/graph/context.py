"""Per-request context injected through RunnableConfig["configurable"]["ctx"]."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from act_agent.auth.rbac import Principal
from act_agent.graph.compress import ContextCompressor
from act_agent.settings import Settings
from act_agent.skills.registry import SkillRegistry
from act_agent.skills.router import SkillRouter


@dataclass
class RunContext:
    principal: Principal
    tools: list
    registry: SkillRegistry
    router: SkillRouter
    compressor: ContextCompressor
    llm: Any
    settings: Settings
    session_id: str
    thread_id: str
    request_id: str | None = None
    middleware: Any = field(default=None, repr=False)
    _tool_map: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.middleware is None:
            from act_agent.middleware import default_chain

            self.middleware = default_chain()

    @property
    def tool_map(self) -> dict[str, Any]:
        if not self._tool_map:
            self._tool_map = {t.name: t for t in self.tools}
        return self._tool_map


def get_ctx(config: dict) -> RunContext:
    ctx = (config or {}).get("configurable", {}).get("ctx")
    if ctx is None:
        raise RuntimeError("RunContext missing from config['configurable']['ctx']")
    return ctx
