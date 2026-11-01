from act_agent.auth.rbac import (
    Principal,
    classify_tool,
    filter_tools_for_principal,
    normalize_tool_name,
    required_scope,
    scopes_for_roles,
)


def test_normalize_and_classify():
    assert normalize_tool_name("mcp__act-mcp__create_big_activity") == "create_big_activity"
    assert classify_tool("mcp__act-mcp__create_big_activity_with_products") == "create"
    assert classify_tool("update_big_activity") == "update"
    assert classify_tool("delete_product_activity") == "delete"
    assert classify_tool("list_big_activities") == "read"
    assert classify_tool("get_activity_stats") == "read"


def test_scope_mapping():
    assert required_scope("create") == "activity:write"
    assert required_scope("update") == "activity:write"
    assert required_scope("delete") == "activity:delete"
    assert required_scope("read") == "activity:read"
    assert scopes_for_roles(["viewer"]) == {"activity:read"}
    assert "activity:delete" in scopes_for_roles(["admin"])
    assert scopes_for_roles(["unknown"]) == set()


class _Tool:
    def __init__(self, name):
        self.name = name


def test_tool_filtering_by_role():
    tools = [_Tool("list_big_activities"), _Tool("create_big_activity"),
             _Tool("delete_big_activity")]
    viewer = Principal("u", "t", ("viewer",), frozenset({"activity:read"}))
    operator = Principal("u", "t", ("operator",),
                         frozenset({"activity:read", "activity:write"}))
    admin = Principal("u", "t", ("admin",),
                      frozenset({"activity:read", "activity:write", "activity:delete"}))
    assert [t.name for t in filter_tools_for_principal(tools, viewer)] == ["list_big_activities"]
    assert len(filter_tools_for_principal(tools, operator)) == 2
    assert len(filter_tools_for_principal(tools, admin)) == 3
