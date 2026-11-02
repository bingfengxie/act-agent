"""Draft collection: LLM 抽取结果的收敛、增量合并与 missing 计算。"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from act_agent.graph.collect import (
    _coerce_extracted,
    _merge_draft,
    compute_missing,
    extract_draft,
)


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[list] = []

    async def ainvoke(self, messages):
        self.prompts.append(messages)
        return AIMessage(content=self.replies.pop(0))


async def test_coerce_maps_chinese_values_and_normalizes_types():
    out = _coerce_extracted(
        {
            "name": " 中秋节11 ",
            "actType": "折扣",
            "audienceScope": "全部用户",
            "budget": "5,000元",
            "startTime": "2026-09-20",
            "products": '[{"poiId": "10001", "skuId": "111", '
            '"productDiscountJson": {"price": 0.7}}]',
        }
    )

    assert out["name"] == "中秋节11"
    assert out["actType"] == 2
    assert out["audienceScope"] == "ALL"
    assert out["budget"] == 5000
    assert out["startTime"] == "2026-09-20"
    assert out["products"] == [
        {"poiId": 10001, "skuId": 111, "productDiscountJson": '{"price": 0.7}'}
    ]


def test_coerce_drops_invalid_values_and_products_without_poi():
    out = _coerce_extracted(
        {
            "actType": 9,
            "audienceScope": "随便",
            "budget": "很多",
            "products": [{"skuId": 1}, {"poiId": "10001"}],
        }
    )

    assert out == {"products": [{"poiId": 10001}]}


def test_merge_keeps_existing_fields_and_tracks_only_changes():
    draft = {"name": "中秋节11", "actType": 2}
    merged = _merge_draft(draft, {"name": "中秋节12", "budget": 5000})

    assert merged == {"name": "中秋节12", "actType": 2, "budget": 5000}


def test_merge_replaces_products_as_a_whole_list():
    draft = {"products": [{"poiId": 1}]}
    merged = _merge_draft(draft, {"products": [{"poiId": 1}, {"poiId": 2}]})

    assert merged["products"] == [{"poiId": 1}, {"poiId": 2}]
    # 空列表不能把已收集的商品清空
    assert _merge_draft(merged, {"products": []})["products"] == [{"poiId": 1}, {"poiId": 2}]


def test_missing_requires_valid_products_for_combined_create():
    draft = {
        "name": "中秋节11",
        "actType": 2,
        "startTime": "2026-09-20",
        "endTime": "2026-09-25",
        "audienceScope": "ALL",
        "budget": 5000,
    }
    assert compute_missing("act-mcp-combined-create", draft) == ["products"]
    assert compute_missing("act-mcp-big-activity", draft) == []
    assert compute_missing("act-mcp-combined-create", {**draft, "products": [{"skuId": 1}]}) == [
        "products"
    ]


async def test_extract_draft_merges_partial_updates_across_turns():
    llm = FakeLLM(
        [
            '{"name": "中秋节11", "actType": "折扣"}',
            '{"budget": "5000", "products": [{"poiId": "10001"}]}',
        ]
    )
    first = await extract_draft(
        llm,
        skill="act-mcp-combined-create",
        task_state={"draft": {}},
        message="活动名称：中秋节11，活动类型：折扣",
    )

    assert first["draft"] == {"name": "中秋节11", "actType": 2}
    assert first["missing"] == [
        "startTime", "endTime", "audienceScope", "budget", "products"
    ]
    assert first["stage"] == "collecting"

    second = await extract_draft(
        llm,
        skill="act-mcp-combined-create",
        task_state={"draft": first["draft"]},
        message="预算 5000，商品 poiId 10001",
    )

    assert second["draft"] == {
        "name": "中秋节11",
        "actType": 2,
        "budget": 5000,
        "products": [{"poiId": 10001}],
    }
    assert "name" not in second["missing"] and "budget" not in second["missing"]
    assert second["missing"] == ["startTime", "endTime", "audienceScope"]


async def test_extract_draft_skips_non_create_skills_and_bad_json():
    llm = FakeLLM(['{"name": "不应被使用"}'])
    assert await extract_draft(
        llm, skill="act-mcp-big-activity-update", task_state={}, message="改成中秋节12"
    ) is None

    bad = FakeLLM(["不是 JSON"])
    assert await extract_draft(
        bad, skill="act-mcp-combined-create", task_state={}, message="随便"
    ) is None


async def test_extract_draft_records_corrections():
    update = await extract_draft(
        FakeLLM(['{"name": "中秋节12"}']),
        skill="act-mcp-combined-create",
        task_state={"draft": {"name": "中秋节11"}, "corrections": []},
        message="名字改成中秋节12",
    )

    assert update["draft"]["name"] == "中秋节12"
    assert update["corrections"] == ["name"]
