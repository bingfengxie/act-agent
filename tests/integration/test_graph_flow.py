"""End-to-end graph behaviour with a scripted LLM and a fake MCP tool.

Covers: routing -> tool call -> runtime preview -> interrupt -> (clarify) ->
confirm -> execute -> audit.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select

from act_agent.auth.rbac import Principal
from act_agent.graph.builder import build_graph
from act_agent.graph.compress import ContextCompressor
from act_agent.graph.context import RunContext
from act_agent.skills.registry import SkillRegistry
from act_agent.skills.router import SkillRouter
from act_agent.storage.db import get_session_factory
from act_agent.storage.models import AuditLog

ACTIVITY_ARGS = {
    "name": "中秋节3",
    "actType": 2,
    "startTime": "2026-08-10T00:00",
    "endTime": "2026-08-15T23:59",
    "audienceScope": "ALL",
    "budget": 1212,
    "collectionDiscountJson": "{}",
    "productActivitiesJson": json.dumps(
        [
            {"poiId": "10001", "skuId": "1111", "upc": "xxxx",
             "productDiscountJson": json.dumps({"price": 0.7})},
            {"poiId": "10002", "skuId": "2222", "upc": "yyyy",
             "productDiscountJson": json.dumps({"price": 0.7})},
        ],
        ensure_ascii=False,
    ),
    "operator": "mcp-agent",
}


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if not self._responses:
            return AIMessage(content="（无更多脚本）")
        return self._responses.pop(0)


class FakeWriteTool:
    name = "create_big_activity_with_products"
    description = "一次创建大活动和多个商品活动"

    def __init__(self):
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return json.dumps(
            {
                "bigActivity": {"id": 99, "name": args.get("name"), "actType": 2},
                "productActivities": [{"id": 100}, {"id": 101}],
            },
            ensure_ascii=False,
        )


def _principal(roles=("operator",)) -> Principal:
    scopes = {"activity:read", "activity:write"}
    if "admin" in roles:
        scopes |= {"activity:delete", "audit:read", "tenant:admin"}
    return Principal(
        user_id="u1", tenant_id="t1", roles=tuple(roles), scopes=frozenset(scopes)
    )


def _build(settings, principal, responses):
    registry = SkillRegistry(settings.resolved_skills_dir)
    registry.discover()
    tool = FakeWriteTool()
    llm = ScriptedLLM(responses)
    ctx = RunContext(
        principal=principal,
        tools=[tool],
        registry=registry,
        router=SkillRouter(registry, llm=None),
        compressor=ContextCompressor(None, settings),
        llm=llm,
        settings=settings,
        session_id="sess-1",
        thread_id="thread-1",
    )
    graph = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "thread-1", "ctx": ctx}, "recursion_limit": 20}
    return graph, tool, config


async def _interrupts(graph, config, inp):
    found = []
    async for chunk in graph.astream(inp, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            found.append(chunk["__interrupt__"][0].value)
    return found


async def test_preview_then_confirm_creates_activity(settings):
    llm_responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "create_big_activity_with_products",
                         "args": ACTIVITY_ARGS, "id": "call_1"}],
        ),
        AIMessage(content="已创建大活动 中秋节3（ID 99），包含 2 个商品活动。"),
    ]
    graph, tool, config = _build(settings, _principal(), llm_responses)

    interrupts = await _interrupts(
        graph, config,
        {"messages": [HumanMessage(content="一次性创建活动，名称中秋节3，折扣，带两个商品")]},
    )
    assert len(interrupts) == 1
    payload = interrupts[0]
    assert payload["type"] == "confirmation"
    assert "中秋节3" in payload["markdown"]
    assert "10001" in payload["markdown"] and "10002" in payload["markdown"]
    assert "2 条" in payload["markdown"]
    assert tool.calls == []  # nothing written before approval

    more = await _interrupts(graph, config, Command(resume="确认创建 中秋节3"))
    assert more == []
    assert len(tool.calls) == 1
    assert tool.calls[0]["name"] == "中秋节3"

    snapshot = await graph.aget_state(config)
    assert snapshot.values["task_state"]["stage"] == "completed"
    last = snapshot.values["messages"][-1]
    assert "已创建" in last.content

    async with get_session_factory()() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tool_name == "create_big_activity_with_products"
    assert rows[0].action == "create"
    assert rows[0].status == "success"
    assert rows[0].tenant_id == "t1"


async def test_ambiguous_reply_asks_clarification_once(settings):
    responses = [
        AIMessage(content="", tool_calls=[{"name": "create_big_activity_with_products",
                                           "args": ACTIVITY_ARGS, "id": "call_1"}]),
        AIMessage(content="已创建。"),
    ]
    graph, tool, config = _build(settings, _principal(), responses)

    first = await _interrupts(
        graph, config, {"messages": [HumanMessage(content="一次性创建中秋节3活动")]}
    )
    assert first[0]["type"] == "confirmation"

    clarify = await _interrupts(graph, config, Command(resume="好"))
    assert len(clarify) == 1
    assert clarify[0]["type"] == "clarify"
    assert tool.calls == []
    # 追问只发简短提示，不重复整张预览卡片
    assert "商品活动（2 条）" not in clarify[0]["markdown"]
    assert "未执行任何写操作" in clarify[0]["markdown"]

    done = await _interrupts(graph, config, Command(resume="确认创建 中秋节3"))
    assert done == []
    assert len(tool.calls) == 1


async def test_cancel_writes_nothing(settings):
    responses = [
        AIMessage(content="", tool_calls=[{"name": "create_big_activity_with_products",
                                           "args": ACTIVITY_ARGS, "id": "call_1"}]),
        AIMessage(content="已取消，未写入任何数据。"),
    ]
    graph, tool, config = _build(settings, _principal(), responses)

    await _interrupts(graph, config, {"messages": [HumanMessage(content="一次性创建活动")]})
    await _interrupts(graph, config, Command(resume="取消"))

    assert tool.calls == []
    snapshot = await graph.aget_state(config)
    assert snapshot.values["task_state"]["stage"] == "cancelled"

    async with get_session_factory()() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "cancelled"


async def test_viewer_cannot_execute_write_tool(settings):
    responses = [
        AIMessage(content="", tool_calls=[{"name": "create_big_activity_with_products",
                                           "args": ACTIVITY_ARGS, "id": "call_1"}]),
        AIMessage(content="权限不足。"),
    ]
    viewer = Principal("u2", "t1", ("viewer",), frozenset({"activity:read"}))
    graph, tool, config = _build(settings, viewer, responses)

    await _interrupts(graph, config, {"messages": [HumanMessage(content="一次性创建活动")]})
    await _interrupts(graph, config, Command(resume="确认创建 中秋节3"))

    assert tool.calls == []
    async with get_session_factory()() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "denied"


async def test_date_only_times_are_normalized_before_preview_and_execution(settings):
    date_only_args = {**ACTIVITY_ARGS, "startTime": "2026-8-10", "endTime": "2026-8-15"}
    responses = [
        AIMessage(content="", tool_calls=[{"name": "create_big_activity_with_products",
                                           "args": date_only_args, "id": "call_1"}]),
        AIMessage(content="已创建。"),
    ]
    graph, tool, config = _build(settings, _principal(), responses)

    interrupts = await _interrupts(
        graph, config, {"messages": [HumanMessage(content="一次性创建中秋节3活动")]}
    )
    assert "2026-08-10T00:00" in interrupts[0]["markdown"]
    assert "2026-08-15T23:59" in interrupts[0]["markdown"]

    await _interrupts(graph, config, Command(resume="确认创建 中秋节3"))
    assert tool.calls[0]["startTime"] == "2026-08-10T00:00"
    assert tool.calls[0]["endTime"] == "2026-08-15T23:59"


@pytest.mark.parametrize("answer", ["y", "行", "可以"])
async def test_vague_answers_never_write(settings, answer):
    responses = [
        AIMessage(content="", tool_calls=[{"name": "create_big_activity_with_products",
                                           "args": ACTIVITY_ARGS, "id": "call_1"}]),
        AIMessage(content="请明确确认。"),
    ]
    graph, tool, config = _build(settings, _principal(), responses)
    await _interrupts(graph, config, {"messages": [HumanMessage(content="一次性创建活动")]})
    clarify = await _interrupts(graph, config, Command(resume=answer))
    assert clarify and clarify[0]["type"] == "clarify"
    assert tool.calls == []
