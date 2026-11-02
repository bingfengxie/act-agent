"""Graph state.

`task_state` is the compression-proof core: it is injected into every prompt in
full, regardless of how aggressively `messages` was summarised.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class TaskState(TypedDict, total=False):
    intent: str | None          # create_big_activity_with_products / update_big_activity / ...
    skill: str | None
    stage: str                  # idle/collecting/previewing/awaiting_confirmation/...
    draft: dict[str, Any]       # 已收集的活动参数（权威副本，压缩历史也不会丢）
    missing: list[str]          # 仍缺的字段
    corrections: list[str]      # 用户修正过的字段
    collected_upto: int         # 已做草稿抽取的 messages 长度（避免重复抽取）
    last_tool: str | None
    last_result: str | None     # 最近一次工具结果摘要
    activity_ids: list[str]     # 已创建/涉及的活动 ID


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    summary: str                          # 更早消息的滚动摘要
    summary_upto: int                     # 已摘要到哪条；模型只看 messages[summary_upto:]
    task_state: TaskState
    skill: str | None
