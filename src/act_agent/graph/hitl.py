"""Deterministic confirmation parsing.

The model never decides whether a write may proceed - this parser does.
Deliberately conservative: vague Chinese replies ("好", "可以", "行") and bare
"y"/"n" are ambiguous and get one clarification round, exactly as the skills require.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["confirm", "cancel", "ambiguous"]

# 取消语义可以出现在句子任意位置（"取消本次操作"）
_CANCEL_CN = re.compile(r"(取消|不建了|不删了|不改了|先不|算了|停止|别建|别删|别改)")
_CANCEL_EN = re.compile(r"^\s*(cancel|no|stop|abort)\b", re.I)

# 确认必须是整句匹配，避免 "确认一下时间" 被误判为批准
_CONFIRM_CN = re.compile(
    r"^(确认创建|确认修改|确认删除|确认执行|确认提交|确认)(\s+\S.*)?$"
)
_CONFIRM_EN = re.compile(
    r"^(yes|ok|confirm|confirmed|submit)"
    r"([, ]+(create|update|delete|submit|it)(\s+it)?)?[.!]?$",
    re.I,
)

# 中文含糊回复：语义上像同意，但不构成明确批准
_AMBIGUOUS_CN = re.compile(r"^\s*(好|好的|嗯|恩|可以|行|没问题|看着办|你决定|随便)")
_AMBIGUOUS_EN = re.compile(r"^\s*(y|n|maybe|sure|fine)\s*[.!]?\s*$", re.I)

_TRAILING_PUNCT = "。．.！!，,；;~～ "


@dataclass(frozen=True)
class Confirmation:
    verdict: Verdict
    reason: str


def parse_confirmation(text: str | None) -> Confirmation:
    if text is None:
        return Confirmation("ambiguous", "空回复")
    value = text.strip()
    if not value:
        return Confirmation("ambiguous", "空回复")

    if _CANCEL_CN.search(value) or _CANCEL_EN.search(value):
        return Confirmation("cancel", "命中取消语义")
    if _AMBIGUOUS_CN.search(value) or _AMBIGUOUS_EN.match(value):
        return Confirmation("ambiguous", "回复含糊，缺少明确确认语")

    normalized = value.rstrip(_TRAILING_PUNCT)
    if _CONFIRM_CN.match(normalized) or _CONFIRM_EN.match(normalized):
        return Confirmation("confirm", "明确确认")
    return Confirmation("ambiguous", "未识别为确认或取消")
