"""Structured draft collection: 把用户口述转成可持久化、可注入的 ``task_state.draft``。

为什么需要它：压缩是破坏性的（已摘要的消息会从 ``messages`` 中真正删除），只靠
``messages`` 记忆的「已记录」字段会丢。``task_state`` 每轮完整注入 system prompt，
且随 checkpoint 落库，因此把已收集参数固化到 ``task_state.draft`` 后，跨轮、跨 worker、
压缩后都不会丢。

抽取策略：本轮用户消息 + 已有 draft → 一次轻量 LLM 调用（严格 JSON）→ 增量合并。
只合并用户本轮明确给出/修正的字段，未提到的字段保持原值；``missing`` 由必填表确定性计算。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# 只有「新建活动」类任务需要草稿收集；更新/删除的目标与字段语义不同，不在此处理。
DRAFT_SKILLS = frozenset({"act-mcp-combined-create", "act-mcp-big-activity"})

# skill -> 必填字段（顺序即追问顺序）
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "act-mcp-combined-create": (
        "name",
        "actType",
        "startTime",
        "endTime",
        "audienceScope",
        "budget",
        "products",
    ),
    "act-mcp-big-activity": (
        "name",
        "actType",
        "startTime",
        "endTime",
        "audienceScope",
        "budget",
    ),
}

FIELD_LABELS = {
    "name": "活动名称",
    "actType": "活动类型",
    "startTime": "开始时间",
    "endTime": "结束时间",
    "audienceScope": "活动人群",
    "budget": "预算",
    "products": "商品活动（至少 1 条，需 poiId）",
}

_ACT_TYPE_NAMES = (
    ("组合优惠", 5),
    ("组合", 5),
    ("满减", 1),
    ("折扣", 2),
    ("秒杀", 3),
    ("优惠券", 4),
)

_AUDIENCE_NAMES = (
    ("新客", "NEW"),
    ("新用户", "NEW"),
    ("new", "NEW"),
    ("全部用户", "ALL"),
    ("所有用户", "ALL"),
    ("全部", "ALL"),
    ("所有", "ALL"),
    ("all", "ALL"),
)

EXTRACT_SYSTEM = """你是活动参数抽取器。把用户本轮消息中的活动信息抽取成 JSON，用于维护结构化草稿。

输出规则：
- 只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释。
- 只输出本轮用户**明确给出或修改**的字段；用户没提到的字段不要输出。
- 不要编造，不要补默认值，不要推断用户没说的时间、金额或类型。
- 用户只是确认、取消、闲聊或提问时，输出 {{}}。

字段与格式：
- name: 活动名称（字符串）
- actType: 活动类型整数：满减=1，折扣=2，秒杀=3，优惠券=4，组合优惠=5
- startTime / endTime: 字符串。能确定时刻用 yyyy-MM-ddTHH:mm；只给日期就输出 yyyy-MM-dd
- audienceScope: "ALL"（全部/所有用户）或 "NEW"（新客）
- budget: 数字，单位元
- products: 商品活动数组，元素形如
  {{"poiId": <必填，数字或字符串>, "skuId": <可选>, "upc": <可选>,
   "productDiscountJson": <可选，JSON 字符串>}}
  用户新增或修改商品时，输出**合并已有草稿后的完整列表**（已有条目也要带上），不要只给新增的那一条。

已有草稿（仅供参考，不要原样回显）：
{draft}"""


async def extract_draft(
    llm: Any,
    *,
    skill: str | None,
    task_state: dict,
    message: str,
) -> dict | None:
    """调用 LLM 增量抽取，返回可直接合并进 task_state 的更新；不适用时返回 None。"""
    if llm is None or skill not in DRAFT_SKILLS:
        return None

    draft = dict(task_state.get("draft") or {})
    system = EXTRACT_SYSTEM.format(
        draft=json.dumps(draft, ensure_ascii=False, indent=2) if draft else "（空）"
    )
    try:
        response = await llm.ainvoke([SystemMessage(system), HumanMessage(message)])
    except Exception as exc:  # noqa: BLE001 - 抽取失败不能打断 turn
        logger.warning("draft extraction failed: %s", exc)
        return None

    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)
    extracted = _parse_json_object(content)
    if extracted is None:
        logger.warning("draft extraction returned non-JSON: %r", content[:200])
        return None

    updates = _coerce_extracted(extracted)
    merged = _merge_draft(draft, updates)
    corrections = list(task_state.get("corrections") or [])
    for field, value in updates.items():
        if field in draft and draft[field] != value and field not in corrections:
            corrections.append(field)

    missing = compute_missing(skill, merged)
    return {
        "draft": merged,
        "missing": missing,
        "corrections": corrections,
        "stage": "collecting" if missing else "ready",
    }


def compute_missing(skill: str | None, draft: dict) -> list[str]:
    missing: list[str] = []
    for field in REQUIRED_FIELDS.get(skill or "", ()):
        value = draft.get(field)
        if field == "products":
            if not _valid_products(value):
                missing.append(field)
        elif value is None or value == "":
            missing.append(field)
    return missing


def _merge_draft(draft: dict, updates: dict) -> dict:
    merged = dict(draft)
    for field, value in updates.items():
        if field == "products":
            # 商品列表按「整表替换」处理：抽取提示词要求 LLM 返回合并后的完整列表
            if _valid_products(value):
                merged[field] = value
            continue
        if value is None or value == "":
            continue
        merged[field] = value
    return merged


def _coerce_extracted(data: dict) -> dict:
    """把 LLM 可能给出的宽松写法收敛成工具参数格式。"""
    out: dict[str, Any] = {}

    if isinstance(data.get("name"), str) and data["name"].strip():
        out["name"] = data["name"].strip()

    act_type = _to_act_type(data.get("actType"))
    if act_type is not None:
        out["actType"] = act_type

    for key in ("startTime", "endTime"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()

    audience = _to_audience(data.get("audienceScope"))
    if audience is not None:
        out["audienceScope"] = audience

    budget = _to_number(data.get("budget"))
    if budget is not None:
        out["budget"] = budget

    collection = data.get("collectionDiscountJson")
    if collection is not None:
        out["collectionDiscountJson"] = _to_json_string(collection)

    products = _to_products(data.get("products"))
    if products:
        out["products"] = products

    return out


def _to_act_type(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 5 else None
    if isinstance(value, float) and value.is_integer():
        return _to_act_type(int(value))
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _to_act_type(int(text))
        lowered = text.lower()
        for name, code in _ACT_TYPE_NAMES:
            if name in lowered:
                return code
    return None


def _to_audience(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    for name, code in _AUDIENCE_NAMES:
        if name in lowered:
            return code
    return None


def _to_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.replace(",", "").replace("，", "").replace("元", "").strip()
        try:
            number = float(text)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def _to_products(value: Any) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    if not isinstance(value, list):
        return []

    products: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        poi_id = _to_id(item.get("poiId", item.get("poi_id")))
        if poi_id is None:
            continue
        product: dict[str, Any] = {"poiId": poi_id}
        sku_id = _to_id(item.get("skuId", item.get("sku_id")))
        if sku_id is not None:
            product["skuId"] = sku_id
        upc = item.get("upc")
        if isinstance(upc, str) and upc.strip():
            product["upc"] = upc.strip()
        discount = item.get("productDiscountJson", item.get("product_discount_json"))
        if discount is not None:
            product["productDiscountJson"] = _to_json_string(discount)
        products.append(product)
    return products


def _to_id(value: Any) -> int | str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        return int(text) if text.isdigit() else text
    return None


def _to_json_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _valid_products(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, dict) and item.get("poiId") not in (None, "") for item in value
    )


def _parse_json_object(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
