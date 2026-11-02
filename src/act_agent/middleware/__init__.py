"""轻量切面层（middleware）：审计、PII 脱敏等 I/O 边界横切关注点。"""

from __future__ import annotations

from act_agent.middleware.audit import AuditMiddleware
from act_agent.middleware.base import Middleware, MiddlewareChain
from act_agent.middleware.pii import PiiMiddleware, redact_pii, redact_value

__all__ = [
    "AuditMiddleware",
    "Middleware",
    "MiddlewareChain",
    "PiiMiddleware",
    "redact_pii",
    "redact_value",
]


def default_chain() -> MiddlewareChain:
    """默认切面链：仅审计（审计内部已做 PII 脱敏落库）。

    服务层会追加 ``PiiMiddleware`` 以在返回客户端前脱敏最终文本。
    """
    return MiddlewareChain([AuditMiddleware()])


def service_chain() -> MiddlewareChain:
    """服务层切面链：PII 脱敏在前，审计在后（审计写的是脱敏后的值）。"""
    return MiddlewareChain([PiiMiddleware(), AuditMiddleware()])
