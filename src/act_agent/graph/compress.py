"""Non-destructive context compression.

Full history stays in the checkpoint (audit + interrupt replay need it). Only the
view sent to the model is trimmed: a rolling summary of older turns + the last N
messages. Structured `task_state` is injected separately and is never summarised,
so an in-progress activity survives any amount of compression.
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

    def needs_compression(self, messages: list) -> bool:
        if messages_tokens(messages) >= self.settings.compression_threshold:
            return True
        return len(messages) > self.settings.keep_recent_messages * 2

    async def prepare(self, messages: list, summary: str, summary_upto: int) -> tuple[str, int]:
        """Return (summary, split_index). The model only sees messages[split_index:]."""
        if not self.needs_compression(messages):
            return summary, summary_upto

        split = self._split_index(messages)
        if split <= summary_upto:
            return summary, summary_upto

        older = messages[summary_upto:split]
        new_summary = await self._summarize(summary, older)
        logger.info(
            "compressed %d messages into summary (%d -> %d chars)",
            len(older),
            len(summary),
            len(new_summary),
        )
        return new_summary, split

    def _split_index(self, messages: list) -> int:
        keep = self.settings.keep_recent_messages
        idx = max(0, len(messages) - keep)
        # 不能从 ToolMessage 开始，否则会丢失与之配对的 tool_call
        while idx < len(messages) and isinstance(messages[idx], ToolMessage):
            idx += 1
        if idx >= len(messages):
            # 全部剩余都是 tool 结果时，回退到最后一轮对话起点，保证模型至少看到一条消息
            idx = max(0, len(messages) - 1)
            while idx > 0 and isinstance(messages[idx], ToolMessage):
                idx -= 1
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
