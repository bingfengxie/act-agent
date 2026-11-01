"""AgentService: one conversational turn against the LangGraph app.

Emits an event stream that the HTTP layer forwards as SSE:
  start / token / message / interrupt / error / done
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from act_agent.auth.rbac import Principal, filter_tools_for_principal
from act_agent.graph.compress import ContextCompressor
from act_agent.graph.context import RunContext
from act_agent.settings import Settings
from act_agent.skills.registry import SkillRegistry
from act_agent.skills.router import SkillRouter
from act_agent.skills.tools import build_skill_tools

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self, *, settings: Settings, registry: SkillRegistry, mcp, llm, graph) -> None:
        self.settings = settings
        self.registry = registry
        self.mcp = mcp
        self.llm = llm
        self.graph = graph

    async def _build_ctx(
        self,
        *,
        principal: Principal,
        session_id: str,
        thread_id: str,
        request_id: str | None,
        tenant_config: dict | None,
    ) -> RunContext:
        tenant_servers = None
        if tenant_config:
            raw = tenant_config.get("mcpServers") or tenant_config.get("mcp_servers")
            if raw:
                from act_agent.mcp.config import parse_config

                tenant_servers = parse_config(
                    raw if ("mcpServers" in raw or "mcp_servers" in raw)
                    else {"mcpServers": raw}
                )
        mcp_tools = await self.mcp.get_tools(tenant_servers)
        tools = filter_tools_for_principal(mcp_tools, principal) + build_skill_tools(self.registry)
        return RunContext(
            principal=principal,
            tools=tools,
            registry=self.registry,
            router=SkillRouter(self.registry, self.llm),
            compressor=ContextCompressor(self.llm, self.settings),
            llm=self.llm,
            settings=self.settings,
            session_id=session_id,
            thread_id=thread_id,
            request_id=request_id,
        )

    def _config(self, ctx: RunContext, thread_id: str) -> dict:
        return {
            "configurable": {"thread_id": thread_id, "ctx": ctx},
            "recursion_limit": 40,
        }

    async def stream_turn(
        self,
        *,
        principal: Principal,
        session_id: str,
        thread_id: str,
        request_id: str | None = None,
        tenant_config: dict | None = None,
        message: str | None = None,
        resume: str | None = None,
    ) -> AsyncIterator[dict]:
        try:
            ctx = await self._build_ctx(
                principal=principal,
                session_id=session_id,
                thread_id=thread_id,
                request_id=request_id,
                tenant_config=tenant_config,
            )
        except Exception as exc:  # noqa: BLE001 - MCP down should be a clean event
            logger.exception("failed to prepare run context")
            yield {"event": "error", "message": f"无法连接工具服务：{exc}"}
            yield {"event": "done", "status": "error"}
            return

        config = self._config(ctx, thread_id)
        graph_input = (
            {"messages": [HumanMessage(content=message)]}
            if message is not None
            else Command(resume=resume)
        )

        yield {"event": "start", "session_id": session_id, "thread_id": thread_id}

        accumulated = ""
        interrupt_payload: dict | None = None
        failure: str | None = None

        try:
            async for mode, chunk in self.graph.astream(
                graph_input, config, stream_mode=["messages", "updates"]
            ):
                if mode == "messages":
                    msg, meta = chunk
                    if (meta or {}).get("langgraph_node") != "agent":
                        continue
                    content = getattr(msg, "content", "")
                    if isinstance(content, str) and content:
                        accumulated += content
                        yield {"event": "token", "text": content}
                elif mode == "updates":
                    if "__interrupt__" in chunk:
                        intr = chunk["__interrupt__"]
                        first = intr[0] if isinstance(intr, (list, tuple)) and intr else intr
                        interrupt_payload = getattr(first, "value", None) or interrupt_payload
        except GraphRecursionError:
            failure = "任务步骤过多，已中止本轮。请把需求拆小后再试。"
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the client
            logger.exception("graph execution failed")
            failure = f"执行失败：{exc}"

        if failure:
            yield {"event": "error", "message": failure}
            yield {"event": "done", "status": "error"}
            return

        # Authoritative interrupt lookup (covers engines that do not surface it in updates)
        if interrupt_payload is None:
            payload = await self._read_interrupt(config)
            interrupt_payload = payload

        if interrupt_payload:
            # 预览只走 interrupt 事件发送一次；done 只带状态，避免客户端渲染两遍
            yield {"event": "interrupt", "payload": interrupt_payload}
            yield {"event": "done", "status": "awaiting_confirmation"}
            return

        if not accumulated.strip():
            content = await self._last_assistant_text(config)
            if content:
                yield {"event": "message", "content": content}

        yield {"event": "done", "status": "idle"}

    async def _read_interrupt(self, config: dict) -> dict | None:
        try:
            snapshot = await self.graph.aget_state(config)
        except Exception:  # noqa: BLE001
            return None
        for task in getattr(snapshot, "tasks", ()) or ():
            interrupts = getattr(task, "interrupts", ()) or ()
            if interrupts:
                value = getattr(interrupts[0], "value", None)
                if isinstance(value, dict):
                    return value
        return None

    async def _last_assistant_text(self, config: dict) -> str:
        try:
            snapshot = await self.graph.aget_state(config)
        except Exception:  # noqa: BLE001
            return ""
        messages = snapshot.values.get("messages", []) if snapshot.values else []
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content.strip():
                return msg.content
        return ""

    async def get_state(self, *, thread_id: str, principal: Principal) -> dict:
        """Read-only state projection for the API (never exposes another tenant's data)."""
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.graph.aget_state(config)
        values = snapshot.values or {}
        pending = None
        for task in getattr(snapshot, "tasks", ()) or ():
            interrupts = getattr(task, "interrupts", ()) or ()
            if interrupts:
                pending = getattr(interrupts[0], "value", None)
                break
        messages = [
            {
                "role": m.type,
                "content": m.content if isinstance(m.content, str) else str(m.content),
                **({"tool_calls": m.tool_calls} if getattr(m, "tool_calls", None) else {}),
            }
            for m in values.get("messages", [])
        ]
        return {
            "thread_id": thread_id,
            "messages": messages,
            "summary": values.get("summary", ""),
            "summary_upto": values.get("summary_upto", 0),
            "task_state": values.get("task_state", {}),
            "skill": values.get("skill"),
            "pending_interrupt": pending,
        }
