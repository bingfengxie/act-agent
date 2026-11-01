import pytest

from act_agent.graph.hitl import parse_confirmation


@pytest.mark.parametrize(
    "text",
    ["确认创建 中秋节3", "确认创建", "确认修改", "确认删除", "确认", "yes", "Yes, create it",
     "ok", "confirm", "确认创建。"],
)
def test_explicit_confirmation(text):
    assert parse_confirmation(text).verdict == "confirm"


@pytest.mark.parametrize(
    "text",
    ["取消", "先不建了", "算了", "别删", "cancel", "no", "停止本次操作"],
)
def test_cancel(text):
    assert parse_confirmation(text).verdict == "cancel"


@pytest.mark.parametrize(
    "text",
    ["好", "好的", "嗯", "可以", "行", "没问题", "y", "n", "sure", "", None, "确认一下时间"],
)
def test_ambiguous(text):
    assert parse_confirmation(text).verdict == "ambiguous"
