"""Skill routing.

Deterministic keyword rules run first (they cover the real intents cheaply and
never hallucinate). The LLM is only consulted when keywords find nothing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from act_agent.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Ordered: earlier rules win. Each rule maps to a concrete skill or None.
_KEYWORD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"删除|删掉|下?线|清理|移除|delete|remove", re.I), "act-mcp-big-activity-delete"),
    (re.compile(r"编辑|修改|调整|改成|改为|改一下|edit|update|modify", re.I),
     "act-mcp-big-activity-update"),
    (re.compile(r"组合|一次性(创建|新建)|批量创建|大活动.{0,6}商品|商品列表|sku|门店\s*id|"
                r"combined|with\s+products", re.I),
     "act-mcp-combined-create"),
    # 只建大活动（没有商品列表）走组入口 skill，由其公共契约决定用 create_big_activity
    (re.compile(r"新建|创建|新增|建一个|create", re.I), "act-mcp-big-activity"),
]

ROUTER_SYSTEM = """你是意图路由器。根据用户消息，从候选 skill 中选出最匹配的一个。
候选：
{candidates}

只输出 JSON：{{"skill": "<skill name 或 null>", "confidence": 0.0-1.0, "reason": "一句话"}}
规则：
- 用户只是查询活动、看统计、闲聊 → skill = null
- 同时要建大活动和商品 → act-mcp-combined-create
- 要改已有活动 → act-mcp-big-activity-update
- 要删活动 → act-mcp-big-activity-delete
- 只建大活动（无商品）→ act-mcp-big-activity
- 正在进行的任务若只是补充参数/确认，保持当前 skill"""


@dataclass(frozen=True)
class RouteDecision:
    skill: str | None
    reason: str
    confidence: float = 1.0


class SkillRouter:
    def __init__(self, registry: SkillRegistry, llm=None) -> None:
        self.registry = registry
        self.llm = llm

    def _keyword_route(self, message: str) -> RouteDecision | None:
        hits = [(pattern, name) for pattern, name in _KEYWORD_RULES if pattern.search(message)]
        if not hits:
            return None
        # Prefer the first (highest priority) hit that actually exists.
        for _, name in hits:
            if self.registry.get(name):
                return RouteDecision(skill=name, reason="keyword rule", confidence=0.9)
        return None

    async def route(
        self, message: str, current_skill: str | None = None
    ) -> RouteDecision:
        # Turning a confirmation reply ("确认创建 X") must never re-route the task.
        if current_skill and _is_continuation(message):
            return RouteDecision(skill=current_skill, reason="continuation")

        decision = self._keyword_route(message)
        if decision:
            return decision

        if self.llm is None:
            return RouteDecision(skill=None, reason="no keyword match, no llm")

        try:
            return await self._llm_route(message, current_skill)
        except Exception as exc:  # noqa: BLE001 - routing must degrade, not crash
            logger.warning("llm routing failed: %s", exc)
            return RouteDecision(skill=current_skill, reason="llm routing failed")

    async def _llm_route(self, message: str, current_skill: str | None) -> RouteDecision:
        candidates = "\n".join(
            f"- {s.name}: {s.description[:300]}" for s in self.registry.all()
        )
        system = ROUTER_SYSTEM.format(candidates=candidates)
        if current_skill:
            system += f"\n当前会话正在进行的 skill：{current_skill}"
        resp = await self.llm.ainvoke([SystemMessage(system), HumanMessage(message)])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        data = _extract_json(text)
        if not data:
            return RouteDecision(skill=current_skill, reason="unparseable router output")
        skill = data.get("skill")
        if skill not in self.registry._skills:  # noqa: SLF001 - same package
            skill = None
        return RouteDecision(
            skill=skill,
            reason=str(data.get("reason", "llm")),
            confidence=float(data.get("confidence", 0.5)),
        )


_CONTINUATION_CN = re.compile(r"^\s*(确认创建|确认修改|确认删除|确认|取消|不建了|不删了|不改了)")
_CONTINUATION_EN = re.compile(r"^\s*(yes|ok|cancel|no|y|n)\b", re.I)


def _is_continuation(message: str) -> bool:
    if len(message.strip()) > 40:
        return False
    return bool(_CONTINUATION_CN.search(message) or _CONTINUATION_EN.search(message))


def _extract_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
