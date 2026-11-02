"""审计切面的单元测试：事件类型映射 + PII 脱敏落库 + 切面链组装。"""

from types import SimpleNamespace

from sqlalchemy import select

from act_agent.auth.rbac import Principal
from act_agent.middleware import AuditMiddleware, default_chain, service_chain
from act_agent.storage.db import get_session_factory
from act_agent.storage.models import AuditLog


def _ctx() -> SimpleNamespace:
    principal = Principal(
        user_id="u1",
        tenant_id="t1",
        roles=("operator",),
        scopes=frozenset({"activity:read", "activity:write"}),
    )
    return SimpleNamespace(
        principal=principal,
        session_id="sess-1",
        thread_id="thread-1",
        request_id="req-1",
    )


async def _rows() -> list[AuditLog]:
    async with get_session_factory()() as db:
        result = await db.execute(select(AuditLog).order_by(AuditLog.id))
        return list(result.scalars().all())


async def test_on_user_input_audits_redacted_content():
    await AuditMiddleware().on_user_input(_ctx(), "联系我 13812345678")
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].event_type == "user_input"
    assert rows[0].content == "联系我 138****5678"
    assert rows[0].tool_name == ""
    assert rows[0].action == ""


async def test_on_tool_call_audits_read_action_with_redacted_args():
    await AuditMiddleware().on_tool_call(
        _ctx(),
        tool_name="list_big_activities",
        action="read",
        args={"phone": "13812345678", "poiId": "10001"},
        status="success",
        duration_ms=12,
    )
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].event_type == "tool_call"
    assert rows[0].tool_name == "list_big_activities"
    assert rows[0].action == "read"
    assert rows[0].status == "success"
    assert rows[0].args_json == {"phone": "138****5678", "poiId": "10001"}
    assert rows[0].duration_ms == 12


async def test_on_tool_call_records_denied_and_cancelled_status():
    mw = AuditMiddleware()
    await mw.on_tool_call(
        _ctx(), tool_name="delete_big_activity", action="delete", args={}, status="denied",
    )
    await mw.on_tool_call(
        _ctx(), tool_name="delete_big_activity", action="delete", args={}, status="cancelled",
    )
    rows = await _rows()
    assert [r.status for r in rows] == ["denied", "cancelled"]
    assert all(r.event_type == "tool_call" for r in rows)


async def test_on_assistant_output_audits_redacted_content():
    await AuditMiddleware().on_assistant_output(_ctx(), "已创建，联系方式 13812345678")
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].event_type == "assistant_output"
    assert rows[0].content == "已创建，联系方式 138****5678"


async def test_full_trail_is_three_events_in_order():
    mw = AuditMiddleware()
    ctx = _ctx()
    await mw.on_user_input(ctx, "查询 13812345678")
    await mw.on_tool_call(
        ctx, tool_name="list_big_activities", action="read", args={}, status="success",
    )
    await mw.on_assistant_output(ctx, "结果如下")
    rows = await _rows()
    assert [r.event_type for r in rows] == ["user_input", "tool_call", "assistant_output"]


async def test_chain_composition():
    assert [mw.name for mw in service_chain()._mws] == ["pii", "audit"]  # noqa: SLF001
    assert [mw.name for mw in default_chain()._mws] == ["audit"]  # noqa: SLF001
