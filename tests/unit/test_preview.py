import json

from act_agent.graph.preview import render_preview


def test_combined_create_preview_renders_products_and_labels():
    args = {
        "name": "中秋节3",
        "actType": 2,
        "startTime": "2026-08-10T00:00",
        "endTime": "2026-08-15T23:59",
        "audienceScope": "ALL",
        "budget": 1212,
        "productActivitiesJson": json.dumps(
            [{"poiId": "10001", "skuId": "1111", "upc": "xxxx",
              "productDiscountJson": json.dumps({"price": 0.7})}]
        ),
    }
    preview = render_preview("mcp__act-mcp__create_big_activity_with_products", args)
    assert "中秋节3" in preview["markdown"]
    assert "2（折扣）" in preview["markdown"]
    assert "ALL（所有用户）" in preview["markdown"]
    assert "10001" in preview["markdown"]
    assert "商品活动（1 条）" in preview["markdown"]
    assert preview["product_count"] == 1


def test_delete_preview_warns_cascade():
    preview = render_preview("delete_big_activity", {"actId": 11})
    assert "11" in preview["markdown"]
    assert "级联删除" in preview["markdown"]
