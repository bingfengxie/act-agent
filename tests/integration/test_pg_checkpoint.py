"""Distributed checkpoint proof: state written by one saver instance is visible
to a completely independent saver instance (different connection, same store).

Skipped unless a Postgres is reachable (docker compose up -d postgres).
"""

from __future__ import annotations

import json
import os
import socket
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from act_agent.auth.rbac import Principal
from act_agent.graph.builder import build_graph
from act_agent.graph.compress import ContextCompressor
from act_agent.graph.context import RunContext
from act_agent.skills.registry import SkillRegistry
from act_agent.skills.router import SkillRouter
from act_agent.storage.checkpointer import CheckpointerManager

PG_DSN = os.environ.get(
    "ACT_AGENT_TEST_PG_DSN", "postgresql://act:act@localhost:5432/act_agent"
)


def _pg_available() -> bool:
    host, port = "localhost", 5432
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="postgres not reachable")


class _ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        system = messages[0].content if messages else ""
        if isinstance(system, str) and "活动参数抽取器" in system:
            return AIMessage(content="{}")
        return self._responses.pop(0)


class _FakeTool:
    name = "create_big_activity_with_products"
    description = "create"

    def __init__(self):
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return json.dumps({"bigActivity": {"id": 501}}, ensure_ascii=False)


def _ctx(settings, tool, llm) -> RunContext:
    registry = SkillRegistry(settings.resolved_skills_dir)
    registry.discover()
    return RunContext(
        principal=Principal("u1", "t1", ("operator",),
                            frozenset({"activity:read", "activity:write"})),
        tools=[tool],
        registry=registry,
        router=SkillRouter(registry, llm=None),
        compressor=ContextCompressor(None, settings),
        llm=llm,
        settings=settings,
        session_id="pg-session",
        thread_id="pg-thread",
    )


ARGS = {
    "name": "中秋节3",
    "actType": 2,
    "startTime": "2026-08-10T00:00",
    "endTime": "2026-08-15T23:59",
    "audienceScope": "ALL",
    "budget": 1212,
    "productActivitiesJson": "[]",
}


async def test_state_survives_new_checkpointer_instance(settings):
    thread_id = f"pgtest-{uuid.uuid4().hex}"
    tool_a = _FakeTool()
    llm_a = _ScriptedLLM(
        [AIMessage(content="", tool_calls=[{"name": tool_a.name, "args": ARGS, "id": "c1"}])]
    )

    saver_a = CheckpointerManager(PG_DSN)
    await saver_a.start()
    try:
        graph_a = build_graph(saver_a.saver)
        ctx_a = _ctx(settings, tool_a, llm_a)
        config_a: RunnableConfig = {
            "configurable": {"thread_id": thread_id, "ctx": ctx_a},
            "recursion_limit": 10,
        }
        async for chunk in graph_a.astream(
            {"messages": [HumanMessage(content="一次性创建中秋节3活动，带商品")]},
            config_a,
            stream_mode="updates",
        ):
            if "__interrupt__" in chunk:
                assert "中秋节3" in chunk["__interrupt__"][0].value["markdown"]
    finally:
        await saver_a.stop()

    # 全新的连接 + 全新的图，只靠共享的 Postgres 恢复
    tool_b = _FakeTool()
    llm_b = _ScriptedLLM(
        [AIMessage(content="已创建大活动 中秋节3（ID 501）。")]
    )
    saver_b = CheckpointerManager(PG_DSN)
    await saver_b.start()
    try:
        graph_b = build_graph(saver_b.saver)
        ctx_b = _ctx(settings, tool_b, llm_b)
        config_b: RunnableConfig = {
            "configurable": {"thread_id": thread_id, "ctx": ctx_b},
            "recursion_limit": 10,
        }

        before = await graph_b.aget_state(config_b)
        assert before.values["task_state"]["skill"] == "act-mcp-combined-create"
        assert len(before.values["messages"]) >= 1

        async for _ in graph_b.astream(
            Command(resume="确认创建 中秋节3"), config_b, stream_mode="updates"
        ):
            pass

        after = await graph_b.aget_state(config_b)
        assert after.values["task_state"]["stage"] == "completed"
        assert len(tool_b.calls) == 1

        await saver_b.saver.adelete_thread(thread_id)
    finally:
        await saver_b.stop()
