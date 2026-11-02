"""PII 脱敏切面：手机号 / 身份证 / 邮箱 / 银行卡。

只作用于「审计落库」与「返回给客户端的最终文本」；checkpoint 历史保留原文以便 replay，
模型输入不脱敏（活动参数里可能合法携带手机号/操作人信息，脱敏会破坏功能）。
"""

from __future__ import annotations

import re
from typing import Any

from act_agent.middleware.base import Middleware

# 手机号：1[3-9] + 9 位数字，前后不接数字
_CN_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
# 身份证：6 位地区 + 8 位生日 + 3 位顺序 + 1 位校验（含 X）
_CN_ID = re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[\dXx])(?!\d)")
# 邮箱：保留首字符，其余本地部分打码
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# 银行卡：12~20 位数字，4 位一组（可含空格），保留首尾
_BANK = re.compile(r"(?<!\d)(\d{4})(?:\s?\d{4}){2,4}(?!\d)")


def redact_pii(text: str) -> str:
    """对单个字符串做 PII 打码，非字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    text = _CN_PHONE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)
    text = _CN_ID.sub(r"\1********\3", text)
    text = _EMAIL.sub(r"\1***@\2", text)
    text = _BANK.sub(lambda m: m.group(0)[:4] + " **** **** " + m.group(0)[-4:], text)
    return text


def redact_value(value: Any) -> Any:
    """递归脱敏任意结构（str / list / dict）。"""
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


class PiiMiddleware(Middleware):
    """在 I/O 边界做脱敏。"""

    name = "pii"

    def redact_input(self, text: str) -> str:
        return redact_pii(text)

    def redact_output(self, text: str) -> str:
        return redact_pii(text)
