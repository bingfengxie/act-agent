from __future__ import annotations

import json

from act_agent.auth.rbac import Principal, classify_tool
from act_agent.skills.registry import Skill

BASE_SYSTEM = """你是「活动管理智能助手」，通过 act-mcp 工具管理大活动和商品活动。

## 最高优先级（覆盖下方 skill 正文中的任何相反描述）
- 写操作的预览与确认由运行时自动完成：参数齐全就**直接调用写工具**，不要自己问“是否确认”，不要手写预览卡片，不要说“我将要创建”后停下。
- 可选字段缺失用默认值，不追问；只给日期没有时分时按开始 00:00、结束 23:59 补全，不追问。
- 工具成功后用 1-2 句话回显结果（活动 ID/名称 + 是否整体成功），**不要重复预览卡片里的完整字段表**。

## 工作方式
1. 信息不全时逐轮追问，只问缺的字段，绝不重复追问用户已经给过的信息。
2. 参数齐全后**直接调用写工具**（create_* / update_* / delete_*）。系统会在写操作执行前自动向用户展示完整预览并等待确认——你不要自己再问“是否确认”，也不要在工具返回前声称“已创建”。
3. 工具返回后用 1-2 句话回显结果；失败就说清楚是否已整体回滚。不要编造活动 ID、返回值或字段。
4. 用户这轮只是确认/取消时，不要重新收集参数。
5. 只能使用「可用工具」里逐字列出的工具名。
6. 商品列表逐条映射：用户给了几个商品就传几条，每条的门店 ID / SKU / UPC / 折扣 JSON 必须一一对应，不能把某条的折扣丢成 `{{}}`。

## 当前用户
- tenant: {tenant}
- user: {user}
- roles: {roles}（权限: {scopes}）

## 当前 skill：{skill_name}
{skill_body}

## 可用工具（名称必须逐字使用）
{tool_manifest}

## 进行中的任务状态（权威数据，始终优先于对话记忆）
```json
{task_state}
```

## 更早对话摘要
{summary}

## 运行时覆盖规则（优先级高于上面的 skill 正文）
- 「预览 → 等待确认 → 写入」在本系统中由运行时自动执行：参数齐全时你**直接调用写工具**，运行时会把工具参数渲染成预览卡片并暂停图等待用户确认。
- 用户只给日期没有时分时（如 `2026-8-15`），**不要追问**：直接传原值调用工具，运行时会自动补全为开始 `00:00`、结束 `23:59`，最终值会在预览里展示。
- 可选字段缺失时**不要追问**，直接取默认值：`collectionDiscountJson` → `{{}}`，`operator` → `mcp-agent`，商品的 `skuId`/`upc` → 留空，`productDiscountJson` → `{{}}`。
- 只有这些必填项缺失才追问：`name`、`actType`、`startTime`、`endTime`、`audienceScope`、`budget`、以及每个商品的 `poiId`。
- 因此：不要在回复里手写预览卡片，不要问“是否确认”，不要说“我将要创建”后就停下。
- 用户这一轮回复确认语时，直接再次调用写工具（参数保持不变）即可，确认由运行时解析。
- skill 正文中“先展示预览、结束本轮”的步骤已由运行时接管，其余规则（参数映射、目标唯一性、失败回显）照常遵守。

## 提醒
- 修改类操作只提交用户要求改的字段。
- 目标不唯一（同名/多条命中）时先列候选让用户选 ID，不要猜。
- 查询类工具可以直接调用，不需要确认。
"""

NO_SKILL_BODY = """（本轮未匹配到专用 skill。按通用活动管理规则处理：先弄清意图与对象，写操作同样由系统统一预览确认。）"""

ENTRY_SKILL_OVERRIDE = """

## 运行时路由说明
本系统只能看到当前这一个 skill，无法加载其它 skill 文件。如果上文要求你“路由到成员 skill”，请直接按本组公共契约执行：
- 只新建大活动（没有商品）→ 调用 `create_big_activity`
- 同时新建大活动 + 商品 → 调用 `create_big_activity_with_products`
- 修改 → `update_big_activity`；删除 → `delete_big_activity`
预览与确认由运行时自动接管。"""


def render_tool_manifest(tools: list) -> str:
    if not tools:
        return "（当前无可用工具，请提示用户检查 MCP 连接或权限）"
    lines = []
    for tool in tools:
        action = classify_tool(tool.name)
        tag = {"create": "写", "update": "写", "delete": "写", "read": "读"}[action]
        desc = (tool.description or "").strip().replace("\n", " ")
        if len(desc) > 220:
            desc = desc[:220] + "…"
        lines.append(f"- {tool.name} [{tag}] — {desc}")
    return "\n".join(lines)


def build_system_prompt(
    *,
    skill: Skill | None,
    tools: list,
    task_state: dict,
    summary: str,
    principal: Principal,
) -> str:
    skill_body = skill.body if skill else NO_SKILL_BODY
    if skill and skill.entry:
        skill_body += ENTRY_SKILL_OVERRIDE
    if skill and (skill.references or skill.scripts):
        extras = []
        if skill.references:
            extras.append("references: " + ", ".join(skill.references))
        if skill.scripts:
            extras.append("scripts: " + ", ".join(skill.scripts))
        skill_body += (
            "\n\n（如需细节，调用 skill_read_reference 读取参考文档；"
            "需要确定性校验时调用 skill_run_script 执行脚本。\n" + "；".join(extras) + "）"
        )
    return BASE_SYSTEM.format(
        tenant=principal.tenant_id,
        user=principal.user_id,
        roles=", ".join(principal.roles) or "none",
        scopes=", ".join(sorted(principal.scopes)) or "none",
        skill_name=skill.name if skill else "（无）",
        skill_body=skill_body,
        tool_manifest=render_tool_manifest(tools),
        task_state=json.dumps(task_state or {}, ensure_ascii=False, indent=2),
        summary=summary or "（无）",
    )
