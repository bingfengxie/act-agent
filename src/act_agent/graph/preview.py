"""Render a human-readable preview card for a pending write tool call.

The preview is produced by the *runtime*, not the model, so it cannot be
paraphrased away or skipped. It is what the user approves.
"""

from __future__ import annotations

import json
from typing import Any

ACT_TYPE_LABELS = {
    1: "1（满减）",
    2: "2（折扣）",
    3: "3（秒杀）",
    4: "4（优惠券）",
    5: "5（组合优惠）",
}
AUDIENCE_LABELS = {"ALL": "ALL（所有用户）", "NEW": "NEW（新客）"}

_CREATE_BIG_FIELDS = [
    ("name", "名称"),
    ("actType", "活动类型"),
    ("startTime", "开始时间"),
    ("endTime", "结束时间"),
    ("audienceScope", "活动人群"),
    ("budget", "预算(元)"),
    ("collectionDiscountJson", "集合优惠 JSON"),
    ("operator", "操作人"),
]


def _pretty(value: Any) -> str:
    if value is None:
        return "（空）"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _create_big_activity_table(args: dict[str, Any]) -> str:
    rows = ["| 字段 | 值 |", "| --- | --- |"]
    for key, label in _CREATE_BIG_FIELDS:
        if key not in args:
            continue
        value = args[key]
        if key == "actType":
            value = ACT_TYPE_LABELS.get(int(value), value) if str(value).isdigit() else value
        elif key == "audienceScope":
            value = AUDIENCE_LABELS.get(str(value), value)
        rows.append(f"| {label} | {_pretty(value)} |")
    return "\n".join(rows)


def _product_table(products: list[dict[str, Any]]) -> str:
    if not products:
        return "（无商品）"
    rows = ["| # | POI | SKU | UPC | 商品优惠 |", "| --- | --- | --- | --- | --- |"]
    for idx, item in enumerate(products, start=1):
        discount = _maybe_json(item.get("productDiscountJson", "{}"))
        rows.append(
            f"| {idx} | {item.get('poiId', '')} | {item.get('skuId', '')} | "
            f"{item.get('upc', '')} | `{_pretty(discount)}` |"
        )
    return "\n".join(rows)


def render_preview(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return {"title", "markdown", "tool", "args"} for the confirmation interrupt."""
    name = tool_name.split("__")[-1]

    if name == "create_big_activity_with_products":
        products = _maybe_json(args.get("productActivitiesJson", "[]"))
        if not isinstance(products, list):
            products = []
        md = (
            "**大活动**\n\n"
            f"{_create_big_activity_table(args)}\n\n"
            f"**商品活动（{len(products)} 条）**\n\n"
            f"{_product_table(products)}\n\n"
            "大活动与全部商品活动将在同一事务内创建，任一失败整体回滚。"
        )
        return {
            "title": f"即将创建：{args.get('name', '未命名活动')}",
            "markdown": md,
            "tool": name,
            "args": args,
            "product_count": len(products),
        }

    if name == "create_big_activity":
        md = _create_big_activity_table(args)
        return {
            "title": f"即将创建：{args.get('name', '未命名活动')}",
            "markdown": md,
            "tool": name,
            "args": args,
        }

    if name == "create_product_activity":
        md = _product_table([args])
        return {
            "title": f"即将为活动 {args.get('actId', '')} 添加商品活动",
            "markdown": md,
            "tool": name,
            "args": args,
        }

    if name == "update_big_activity":
        rows = ["| 字段 | 新值 |", "| --- | --- |"]
        for key, value in args.items():
            if key in {"actId", "operator"}:
                continue
            if key == "actType" and str(value).isdigit():
                value = ACT_TYPE_LABELS.get(int(value), value)
            elif key == "audienceScope":
                value = AUDIENCE_LABELS.get(str(value), value)
            rows.append(f"| {key} | {_pretty(value)} |")
        md = (
            f"目标大活动：**{args.get('actId', '')}**\n\n"
            + "\n".join(rows)
            + "\n\n未列出的字段保持原值。"
        )
        return {
            "title": f"即将修改大活动 {args.get('actId', '')}",
            "markdown": md,
            "tool": name,
            "args": args,
        }

    if name in {"delete_big_activity", "delete_product_activity"}:
        md = (
            f"删除目标：**{args.get('actId', args.get('id', ''))}**\n\n"
            "删除不可恢复；删除大活动会级联删除其下全部商品活动。\n\n"
            f"```json\n{json.dumps(args, ensure_ascii=False, indent=2)}\n```"
        )
        return {
            "title": "即将删除",
            "markdown": md,
            "tool": name,
            "args": args,
        }

    return {
        "title": f"即将执行写操作：{name}",
        "markdown": f"```json\n{json.dumps(args, ensure_ascii=False, indent=2)}\n```",
        "tool": name,
        "args": args,
    }
