"""Deterministic argument normalisation applied by the runtime before preview/execute.

The model is free to be sloppy about "2026-8-10" vs "2026-08-10T00:00"; the
runtime makes the write deterministic and shows the final value in the preview.
Rule (matches the skills' convention): a date-only start means 00:00, a date-only
end means 23:59.
"""

from __future__ import annotations

import re
from typing import Any

TIME_TOOLS = {
    "create_big_activity",
    "create_big_activity_with_products",
    "update_big_activity",
}

_DATE_ONLY = re.compile(r"^\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*$")
_DATETIME = re.compile(
    r"^\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s*$"
)


def normalize_time_value(value: Any, default_time: str) -> Any:
    if not isinstance(value, str):
        return value
    match = _DATE_ONLY.match(value)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}T{default_time}"
    match = _DATETIME.match(value)
    if match:
        year, month, day, hour, minute = (int(g) for g in match.groups()[:5])
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}"
    return value


def normalize_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    name = tool_name.split("__")[-1]
    if name not in TIME_TOOLS or not isinstance(args, dict):
        return args
    out = dict(args)
    if "startTime" in out:
        out["startTime"] = normalize_time_value(out["startTime"], "00:00")
    if "endTime" in out:
        out["endTime"] = normalize_time_value(out["endTime"], "23:59")
    return out
