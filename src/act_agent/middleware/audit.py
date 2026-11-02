"""审计切面：把 user_input / tool_call / assistant_output 统一落库。

覆盖范围从「仅 MCP 写工具」扩展到：
- 读操作（``classify_tool == "read"``）
- 本地 skill 工具（``skill_read_reference`` / ``skill_run_script``）
- 用户原始输入（message / resume 回答）
- 最终返回给用户的结果

所有落库内容在写入前先做 PII 脱敏，审计表不存明文敏感信息。
"""

from __future__ import annotations

import logging
from typing import Any

from act_agent.middleware.base import Middleware
from act_agent.middleware.pii import redact_pii, redact_value

logger = logging.getLogger(__name__)


class AuditMiddleware(Middleware):
    name = "audit"

    async def on_user_input(self, ctx: Any, text: str) -> None:
        await _write(ctx, event_type="user_input", content=redact_pii(text))

    async def on_tool_call(
        self,
        ctx: Any,
        *,
        tool_name: str,
        action: str,
        args: dict,
        status: str,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        await _write(
            ctx,
            event_type="tool_call",
            tool_name=tool_name,
            action=action,
            args=redact_value(args),
            status=status,
            error=error,
            duration_ms=duration_ms,
        )

    async def on_assistant_output(self, ctx: Any, text: str) -> None:
        await _write(ctx, event_type="assistant_output", content=redact_pii(text))


async def _write(
    ctx: Any,
    *,
    event_type: str,
    tool_name: str | None = None,
    action: str | None = None,
    args: dict | None = None,
    content: str | None = None,
    status: str = "ok",
    error: str | None = None,
    duration_ms: int = 0,
) -> None:
    from act_agent.storage.db import get_session_factory
    from act_agent.storage.repository import write_audit

    try:
        async with get_session_factory()() as db:
            await write_audit(
                db,
                tenant_id=ctx.principal.tenant_id,
                user_id=ctx.principal.user_id,
                session_id=ctx.session_id,
                thread_id=ctx.thread_id,
                event_type=event_type,
                tool_name=tool_name,
                action=action,
                args=args or {},
                content=content,
                status=status,
                error=error,
                request_id=getattr(ctx, "request_id", None),
                duration_ms=duration_ms,
            )
    except Exception:  # noqa: BLE001 - 审计失败不能打断 turn
        logger.exception("audit write failed (%s)", event_type)
