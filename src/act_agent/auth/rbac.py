"""Roles, scopes and tool-level authorisation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---- roles -> scopes -------------------------------------------------------
ROLE_SCOPES: dict[str, set[str]] = {
    "viewer": {"activity:read"},
    "operator": {"activity:read", "activity:write", "activity:delete"},
    "admin": {"activity:read", "activity:write", "activity:delete", "tenant:admin", "audit:read"},
}

SCOPE_READ = "activity:read"
SCOPE_WRITE = "activity:write"
SCOPE_DELETE = "activity:delete"
SCOPE_AUDIT = "audit:read"
SCOPE_ADMIN = "tenant:admin"

WRITE_ACTIONS = {"create", "update", "delete"}

_PREFIX_RE = re.compile(r"^mcp__.+?__")


def scopes_for_roles(roles: list[str]) -> set[str]:
    scopes: set[str] = set()
    for role in roles:
        scopes |= ROLE_SCOPES.get(role, set())
    return scopes


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    roles: tuple[str, ...] = ()
    scopes: frozenset[str] = field(default_factory=frozenset)
    username: str = ""

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def normalize_tool_name(name: str) -> str:
    """Strip MCP server prefixes: `mcp__act-mcp__create_big_activity` -> `create_big_activity`.

    Tool names themselves contain underscores (`create_big_activity`), so only the
    explicit `mcp__<server>__` form is stripped.
    """
    return _PREFIX_RE.sub("", name)


def classify_tool(name: str) -> str:
    """Return create | update | delete | read."""
    lowered = normalize_tool_name(name).lower()
    if "delete" in lowered or "remove" in lowered:
        return "delete"
    if "update" in lowered or "modify" in lowered:
        return "update"
    if "create" in lowered or "batch" in lowered or "submit" in lowered:
        return "create"
    return "read"


def required_scope(action: str) -> str:
    if action == "delete":
        return SCOPE_DELETE
    if action in {"create", "update"}:
        return SCOPE_WRITE
    return SCOPE_READ


def is_write_action(action: str) -> bool:
    return action in WRITE_ACTIONS


def filter_tools_for_principal(tools: list, principal: Principal) -> list:
    """Only expose MCP tools the caller is allowed to invoke."""
    allowed = []
    for tool in tools:
        action = classify_tool(tool.name)
        if principal.has(required_scope(action)):
            allowed.append(tool)
    return allowed
