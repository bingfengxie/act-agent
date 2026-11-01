from act_agent.graph.normalize import normalize_time_value, normalize_tool_args


def test_date_only_start_and_end():
    assert normalize_time_value("2026-8-10", "00:00") == "2026-08-10T00:00"
    assert normalize_time_value("2026-8-15", "23:59") == "2026-08-15T23:59"
    assert normalize_time_value("2026/8/15", "23:59") == "2026-08-15T23:59"
    assert normalize_time_value("2026年8月15日", "23:59") == "2026-08-15T23:59"


def test_datetime_padding_and_seconds():
    assert normalize_time_value("2026-8-10 9:05", "00:00") == "2026-08-10T09:05"
    assert normalize_time_value("2026-8-10 09:05:30", "00:00") == "2026-08-10T09:05"
    assert normalize_time_value("2026-08-10T09:05", "00:00") == "2026-08-10T09:05"


def test_unknown_format_left_alone():
    assert normalize_time_value("下周一", "00:00") == "下周一"
    assert normalize_time_value(123, "00:00") == 123


def test_args_normalized_only_for_time_tools():
    args = {"name": "x", "startTime": "2026-8-10", "endTime": "2026-8-15"}
    out = normalize_tool_args("create_big_activity_with_products", args)
    assert out["startTime"] == "2026-08-10T00:00"
    assert out["endTime"] == "2026-08-15T23:59"
    assert args["startTime"] == "2026-8-10"  # 不改原对象

    untouched = normalize_tool_args("create_product_activity", args)
    assert untouched == args
