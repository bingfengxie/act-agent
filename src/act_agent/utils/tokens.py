"""Dependency-free token estimation.

CJK characters cost ~1.5 tokens, ASCII ~0.25 tokens/char. This is deliberately
approximate: it only decides *when* to compress, and we keep a safety margin.
"""

from __future__ import annotations

import json
import re

_CJK = re.compile(r"[㐀-䶿一-鿿　-〿＀-￯]")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return int(cjk * 1.5 + other / 4) + 1


def message_tokens(message) -> int:
    total = 0
    content = getattr(message, "content", "")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        total += estimate_tokens(json.dumps(content, ensure_ascii=False, default=str))
    for call in getattr(message, "tool_calls", None) or []:
        total += estimate_tokens(json.dumps(call, ensure_ascii=False, default=str))
    total += estimate_tokens(getattr(message, "name", "") or "")
    return total


def messages_tokens(messages: list) -> int:
    return sum(message_tokens(m) for m in messages)
