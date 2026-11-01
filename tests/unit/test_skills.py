import json

from act_agent.skills.registry import SkillRegistry
from act_agent.skills.router import SkillRouter
from act_agent.skills.tools import build_skill_tools


def _registry(settings) -> SkillRegistry:
    registry = SkillRegistry(settings.resolved_skills_dir)
    registry.discover()
    return registry


def test_discovers_all_skills(settings):
    registry = _registry(settings)
    names = {s.name for s in registry.all()}
    assert {
        "act-mcp-big-activity",
        "act-mcp-combined-create",
        "act-mcp-big-activity-update",
        "act-mcp-big-activity-delete",
    } <= names
    combined = registry.get("act-mcp-combined-create")
    assert combined is not None
    assert "组合创建" in combined.body
    assert "validate_request.py" in combined.scripts
    assert "tool-contract.md" in combined.references


async def test_keyword_routing(settings):
    registry = _registry(settings)
    router = SkillRouter(registry, llm=None)
    assert (await router.route("一次性创建活动，带商品列表")).skill == "act-mcp-combined-create"
    assert (await router.route("删除中秋节3")).skill == "act-mcp-big-activity-delete"
    assert (await router.route("把预算改成 2000")).skill == "act-mcp-big-activity-update"
    assert (await router.route("创建一个折扣活动")).skill == "act-mcp-big-activity"
    assert (await router.route("今天天气怎么样")).skill is None


async def test_continuation_keeps_current_skill(settings):
    registry = _registry(settings)
    router = SkillRouter(registry, llm=None)
    decision = await router.route("确认创建 中秋节3", current_skill="act-mcp-combined-create")
    assert decision.skill == "act-mcp-combined-create"
    assert decision.reason == "continuation"


def test_reference_path_traversal_rejected(settings):
    registry = _registry(settings)
    skill = registry.get("act-mcp-combined-create")
    assert skill.reference_path("../../SKILL.md") is None
    assert skill.reference_path("/etc/passwd") is None
    assert skill.reference_path("tool-contract.md") is not None


async def test_run_validation_script(settings, tmp_path):
    registry = _registry(settings)
    tools = {t.name: t for t in build_skill_tools(registry)}
    draft = tmp_path / "draft.json"
    draft.write_text(
        json.dumps(
            {
                "name": "中秋节4",
                "actType": "折扣",
                "startTime": "2026-8-20 00:00",
                "endTime": "2026-8-25 23:59",
                "audienceScope": "全部",
                "budget": 1212,
                "productActivities": [
                    {"poiId": "10003", "skuId": "3333", "upc": "xxxx33",
                     "productDiscountJson": {"price": 0.7}}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = await tools["skill_run_script"].ainvoke(
        {
            "skill_name": "act-mcp-combined-create",
            "script": "validate_request.py",
            "args": [str(draft)],
        }
    )
    payload = json.loads(raw)
    assert payload["exit_code"] == 0, payload
    result = json.loads(payload["stdout"])
    assert result["ok"] is True
    assert result["tool_arguments"]["actType"] == 2
