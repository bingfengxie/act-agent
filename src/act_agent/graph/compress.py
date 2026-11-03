"""Destructive context compaction.

Older turns are folded into a rolling summary and then **removed** from graph
state, so ``messages`` stays bounded by ``keep_recent_messages`` (plus the
current turn). This is what keeps checkpoint writes, memory and the state
projection from growing with session length.

Nothing is lost that the runtime actually needs:

- audit is persisted separately (``middleware/audit.py``);
- replay only needs the messages around a pending interrupt, which are always
  in the retained tail;
- collected activity parameters live in ``task_state.draft``, never in history.

Older *checkpoints* still contain the pre-compaction state, so time-travel to
an earlier turn remains possible until those checkpoint versions are pruned.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from act_agent.settings import Settings
from act_agent.utils.tokens import messages_tokens

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """你在压缩一段「活动管理」对话的历史。请生成更新后的摘要。

必须保留（逐项不要遗漏）：
1. 用户的活动目标与所有已收集参数：名称、类型、开始/结束时间、人群、预算、商品明细（POI/SKU/UPC/折扣）。
2. 用户修正过的字段（原值 → 新值）。
3. 尚未回答的追问、目标不唯一待选择的活动。
4. HITL 阶段：未预览 / 已预览待确认 / 已确认 / 已完成 / 已取消。
5. 已创建或已修改活动的 ID 与工具返回要点。

可以丢弃：寒暄、重复表述、已被修正的旧值（注明“已修正”即可）、冗长工具原始输出。
只输出摘要正文，不超过 {max_tokens} 字。

<已有摘要>
{previous}

<新增对话>
{new_turns}
"""


class ContextCompressor:
    def __init__(self, llm, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    def needs_compaction(self, messages: list) -> bool:
        if messages_tokens(messages) >= self.settings.compression_threshold:
            return True
        return len(messages) > self.settings.keep_recent_messages * 2

    async def compact(self, messages: list, summary: str) -> tuple[str, int]:
        """Return ``(summary, split)``; the caller replaces messages with ``messages[split:]``.

        ``split == 0`` means nothing to do.
        """
        if not self.needs_compaction(messages):
            return summary, 0

        split = self._split_index(messages)
        if split <= 0:
            return summary, 0

        older = messages[:split]
        new_summary = await self._summarize(summary, older)
        logger.info(
            "compacted %d messages into summary (%d -> %d chars)",
            len(older),
            len(summary),
            len(new_summary),
        )
        return new_summary, split

    def fit_view(self, messages: list, budget_tokens: int | None = None) -> list:
        """Last-resort view trimming, without mutating state.

        Compaction already bounds normal turns. This only fires when the current
        turn alone exceeds the model window (e.g. several huge parallel tool
        results): oversized tool contents are shortened in the view only — the
        full result stays in state/checkpoint.
        """
        budget = budget_tokens if budget_tokens is not None else self.settings.max_context_tokens
        total = messages_tokens(messages)
        if total <= budget:
            return messages

        tool_idx = [
            i
            for i, msg in enumerate(messages)
            if isinstance(msg, ToolMessage) and isinstance(msg.content, str) and msg.content
        ]
        if not tool_idx:
            logger.warning("context over budget (%d > %d) with no tool output to trim", total, budget)
            return messages

        tool_set = set(tool_idx)
        rest = messages_tokens([m for i, m in enumerate(messages) if i not in tool_set])
        per_message = max(200, max(budget - rest, 0) // len(tool_idx))
        # 中文按 1.5 token/字保守换算；ASCII 会被截得更狠，但只在兜底路径上发生
        target_chars = max(240, int(per_message / 1.5))

        result = list(messages)
        for i in tool_idx:
            msg = result[i]
            if len(msg.content) > target_chars:
                head = target_chars // 2
                tail = target_chars - head
                result[i] = msg.model_copy(
                    update={
                        "content": (
                            f"{msg.content[:head]}\n"
                            f"…[结果过长，已省略 {len(msg.content) - target_chars} 字符]…\n"
                            f"{msg.content[-tail:]}"
                        )
                    }
                )
        if messages_tokens(result) > budget:
            logger.warning("context still over budget after view trimming")
        return result

    def _split_index(self, messages: list) -> int:
        """How many leading messages to fold into the summary.

        Keeps at most ``keep_recent_messages``, but never drops the current
        turn's ``HumanMessage`` (index ``h``): the split must satisfy
        ``idx <= h``. If the retained tail (older turns + the current turn) is
        still over budget, the split moves forward towards ``h`` to drop more
        of the older turns. If the current turn alone still exceeds the model
        window, ``fit_view`` shortens oversized tool results in the view
        without touching state.
        """
        n = len(messages)
        turn_start = self._current_turn_start(messages)
        idx = min(turn_start, max(0, n - self.settings.keep_recent_messages))
        idx = min(self._advance_past_tools(messages, idx), turn_start)

        budget = self.settings.compression_threshold
        while idx < turn_start and messages_tokens(messages[idx:]) > budget:
            idx = min(self._advance_past_tools(messages, idx + 1), turn_start)
        return idx

    @staticmethod
    def _current_turn_start(messages: list) -> int:
        """Index of the last HumanMessage: the current turn must stay verbatim."""
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                return i
        return 0

    @staticmethod
    def _advance_past_tools(messages: list, idx: int) -> int:
        # 不能从 ToolMessage 开始，否则会丢失与之配对的 tool_call
        while idx < len(messages) and isinstance(messages[idx], ToolMessage):
            idx += 1
        return idx

    async def _summarize(self, previous: str, older: list) -> str:
        if self.llm is None:
            return self._fallback(previous, older)
        prompt = SUMMARY_PROMPT.format(
            max_tokens=self.settings.summary_max_tokens,
            previous=previous or "（无）",
            new_turns=self._render(older),
        )
        try:
            resp = await self.llm.ainvoke(
                [SystemMessage("你是严谨的对话摘要器，只输出摘要正文。"), HumanMessage(prompt)]
            )
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            text = text.strip()
            if not text:
                raise ValueError("empty summary")
            return text
        except Exception as exc:  # noqa: BLE001 - compression must never break the turn
            logger.warning("summarisation failed, using fallback: %s", exc)
            return self._fallback(previous, older)

    @staticmethod
    def _render(messages: list) -> str:
        lines = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                lines.append(f"用户: {_text(msg.content)}")
            elif isinstance(msg, AIMessage):
                if msg.tool_calls:
                    calls = json.dumps(msg.tool_calls, ensure_ascii=False, default=str)
                    lines.append(f"助手(调用工具): {calls}")
                if msg.content:
                    lines.append(f"助手: {_text(msg.content)}")
            elif isinstance(msg, ToolMessage):
                lines.append(f"工具[{msg.name}]: {_text(msg.content)[:500]}")
            else:
                lines.append(f"{msg.type}: {_text(msg.content)}")
        return "\n".join(lines)

    @staticmethod
    def _fallback(previous: str, older: list) -> str:
        """No LLM: keep the previous summary and note how much was elided.

        Critical facts still survive because task_state is injected in full.
        """
        dropped = ContextCompressor._render(older)
        head = previous or "（无历史摘要）"
        return (
            f"{head}\n\n[已省略 {len(older)} 条更早消息，关键参数见任务状态]\n"
            f"最后省略内容片段: {dropped[-800:]}"
        )


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)
