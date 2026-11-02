"""Graph nodes: prepare -> route -> agent -> (tools -> agent)*"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from act_agent.auth.rbac import classify_tool, is_write_action, required_scope
from act_agent.graph.context import RunContext, get_ctx
from act_agent.graph.hitl import parse_confirmation
from act_agent.graph.normalize import normalize_tool_args
from act_agent.graph.preview import render_preview
from act_agent.graph.prompts import build_system_prompt

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 12_000


# --------------------------------------------------------------------------- #
# prepare: compress context
# --------------------------------------------------------------------------- #
async def prepare_node(state: dict, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    messages = state.get("messages", [])
    summary, summary_upto = await ctx.compressor.prepare(
        messages,
        state.get("summary", ""),
        int(state.get("summary_upto", 0) or 0),
    )
    return {"summary": summary, "summary_upto": summary_upto}


# --------------------------------------------------------------------------- #
# route: pick the skill for this turn
# --------------------------------------------------------------------------- #
async def route_node(state: dict, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    message = _last_human_text(state.get("messages", []))
    current_skill = state.get("skill")
    task_state = dict(state.get("task_state") or {})

    decision = await ctx.router.route(message, current_skill=current_skill)
    skill = decision.skill or current_skill

    # 真正切换任务时才清空草稿；同任务续问/确认必须保留
    switched = decision.skill and decision.skill != current_skill
    if switched and decision.reason != "continuation":
        task_state = {"skill": decision.skill, "stage": "collecting", "draft": {},
                      "missing": [], "corrections": [], "activity_ids": []}
    if skill:
        task_state.setdefault("skill", skill)

    logger.info("route: skill=%s current=%s reason=%s", skill, current_skill, decision.reason)
    return {"skill": skill, "task_state": task_state}


# --------------------------------------------------------------------------- #
# agent: one LLM call with tools bound
# --------------------------------------------------------------------------- #
async def agent_node(state: dict, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    skill = ctx.registry.get(state.get("skill"))
    system_prompt = build_system_prompt(
        skill=skill,
        tools=ctx.tools,
        task_state=state.get("task_state") or {},
        summary=state.get("summary", ""),
        principal=ctx.principal,
    )
    split = int(state.get("summary_upto", 0) or 0)
    history = list(state.get("messages", []))[split:]

    model = ctx.llm.bind_tools(ctx.tools) if ctx.tools else ctx.llm
    response = await model.ainvoke([SystemMessage(system_prompt), *history])
    if not isinstance(response, AIMessage):
        response = AIMessage(content=getattr(response, "content", str(response)))
    return {"messages": [response]}


# --------------------------------------------------------------------------- #
# tools: permission check -> HITL interrupt -> execute -> audit
# --------------------------------------------------------------------------- #
async def tools_node(state: dict, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return {}

    task_state = dict(state.get("task_state") or {})
    # 运行时确定性补全（只给日期时：开始 00:00 / 结束 23:59），预览与执行都用补全后的值
    calls = [
        {**call, "args": normalize_tool_args(call["name"], call.get("args") or {})}
        for call in last.tool_calls
    ]
    write_calls = [c for c in calls if is_write_action(classify_tool(c["name"]))]

    if write_calls:
        payload = _build_confirmation_payload(write_calls)
        answer = interrupt(payload)
        confirmation = parse_confirmation(str(answer))
        if confirmation.verdict == "ambiguous":
            answer = interrupt(_build_clarify_payload(payload))
            confirmation = parse_confirmation(str(answer))

        if confirmation.verdict != "confirm":
            await _audit_many(ctx, write_calls, status="cancelled", error=confirmation.reason)
            task_state.update(stage="cancelled", last_result="用户未确认，已取消")
            return {
                "messages": [
                    _tool_message(
                        call,
                        "用户未确认，本次写操作已取消，未写入任何数据。如需要请重新发起。",
                    )
                    for call in calls
                ],
                "task_state": task_state,
            }

    outputs: list[ToolMessage] = []
    for call in calls:
        outputs.append(await _execute_tool(ctx, call, task_state))

    task_state["last_tool"] = calls[-1]["name"]
    return {
        "messages": outputs,
        "task_state": task_state,
    }


async def _execute_tool(ctx: RunContext, call: dict, task_state: dict) -> ToolMessage:
    name = call["name"]
    args = call.get("args") or {}
    action = classify_tool(name)

    tool = ctx.tool_map.get(name)
    if tool is None:
        await _audit(ctx, name, action, args, status="failed", error="tool not found")
        return _tool_message(call, f"错误：工具 {name} 不存在或不可用。")

    if not ctx.principal.has(required_scope(action)):
        await _audit(ctx, name, action, args, status="denied",
                     error=f"missing scope {required_scope(action)}")
        task_state.update(stage="failed", last_result="权限不足")
        return _tool_message(call, f"权限不足：当前角色缺少 {required_scope(action)}。")

    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            tool.ainvoke(args), timeout=ctx.settings.mcp_tool_timeout_seconds
        )
        text = _stringify(result)[:MAX_TOOL_OUTPUT_CHARS]
        status, error = "success", None
    except TimeoutError:
        text = f"工具 {name} 执行超时（{ctx.settings.mcp_tool_timeout_seconds}s）。"
        status, error = "failed", "timeout"
    except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
        logger.exception("tool %s failed", name)
        text = f"工具 {name} 调用失败：{exc}"
        status, error = "failed", str(exc)[:500]

    duration_ms = int((time.perf_counter() - started) * 1000)
    # 全方位审计：读/写/本地工具统一留痕（不再只审计写工具）
    await _audit(ctx, name, action, args, status=status, error=error, duration_ms=duration_ms)
    if is_write_action(action):
        if status == "success":
            task_state.update(stage="completed", last_result=text[:500])
            task_state.setdefault("activity_ids", [])
            task_state["activity_ids"] = _extract_ids(text) or task_state["activity_ids"]
        else:
            task_state.update(stage="failed", last_result=text[:500])

    return _tool_message(call, text)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def should_continue(state: dict) -> str:
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"


def _last_human_text(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _tool_message(call: dict, content: str) -> ToolMessage:
    return ToolMessage(content=content, name=call["name"], tool_call_id=call["id"])


def _stringify(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False, default=str)


def _build_confirmation_payload(calls: list[dict]) -> dict:
    previews = [render_preview(c["name"], c.get("args") or {}) for c in calls]
    markdown = "\n\n---\n\n".join(p["markdown"] for p in previews)
    titles = "；".join(p["title"] for p in previews)
    action = classify_tool(calls[0]["name"])
    verb = {"create": "创建", "update": "修改", "delete": "删除"}.get(action, "执行")
    target = previews[0]["args"].get("name") or previews[0]["args"].get("actId") or ""
    prompt = f"确认请回复：确认{verb} {target}".strip()
    return {
        "type": "confirmation",
        "action": action,
        "title": titles,
        "markdown": markdown
        + f"\n\n确认请回复：**确认{verb} {target}**\n回复其他内容将取消本次操作。",
        "confirm_prompt": prompt,
        "tool_calls": [{"name": c["name"], "args": c.get("args") or {}} for c in calls],
    }


def _build_clarify_payload(previous: dict) -> dict:
    # 追问只发简短提示，不重复整张预览卡片（避免同一个确认信息被回显两次）
    return {
        "type": "clarify",
        "title": "需要明确确认",
        "markdown": (
            "⚠️ 上一条回复不够明确，**未执行任何写操作**。\n"
            f"请回复 **{previous['confirm_prompt']}** 或 **取消**。"
        ),
        "confirm_prompt": previous["confirm_prompt"],
        "tool_calls": previous["tool_calls"],
    }


async def _audit(
    ctx: RunContext,
    tool_name: str,
    action: str,
    args: dict,
    *,
    status: str,
    error: str | None = None,
    duration_ms: int = 0,
) -> None:
    """工具审计统一走切面链（读/写/本地、deny/cancel/failed 都覆盖）。"""
    await ctx.middleware.on_tool_call(
        ctx,
        tool_name=tool_name,
        action=action,
        args=args,
        status=status,
        error=error,
        duration_ms=duration_ms,
    )


async def _audit_many(
    ctx: RunContext, calls: list[dict], *, status: str, error: str | None
) -> None:
    for call in calls:
        await _audit(
            ctx, call["name"], classify_tool(call["name"]), call.get("args") or {},
            status=status, error=error,
        )


def _extract_ids(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    ids: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"id", "actId", "activityId"} and isinstance(value, (str, int)):
                    ids.append(str(value))
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return ids[:20]
