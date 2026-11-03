"""轻量切面层：围绕一次 turn 的 I/O 边界挂载横切关注点。

为什么自研而不是 LangChain 的 ``BaseMiddleware``（``.with_middleware()``）？

- **HITL** 依赖 LangGraph 的 ``interrupt()`` —— 这是图级运行时原语，与 checkpoint 绑定
  （跨 worker 恢复「待确认」状态）。LangChain 的 ``HumanInTheLoopMiddleware`` 工作在
  模型/工具调用边界（``ToolCallRequest``），承载不了这里的「预览卡片 + confirm/clarify/cancel
  状态机 + 跨轮恢复」，因此 HITL 保持图级实现（见 ``graph/nodes.py``）。
- **摘要** 要真正删除 graph state 里已摘要的消息（``RemoveMessage``），并同步修正
  ``task_state.collected_upto`` 游标；这是状态管理而非模型 I/O，因此保持图级实现
  （见 ``graph/compress.py`` + ``graph/nodes.compact_node``）。

这里只承载真正属于 I/O 边界的能力：**审计（全事件）** 与 **PII 脱敏**。
"""

from __future__ import annotations

from typing import Any


class Middleware:
    """切面基类：每个钩子默认透传/空操作，子类按需覆盖。"""

    name = "middleware"

    def redact_input(self, text: str) -> str:
        """脱敏用户输入（进入审计/日志之前）。默认不脱敏。"""
        return text

    def redact_output(self, text: str) -> str:
        """脱敏最终返回给客户端的文本。默认不脱敏。"""
        return text

    async def on_user_input(self, ctx: Any, text: str) -> None:
        """用户原始输入落点（message 或 resume 回答）。"""

    async def on_tool_call(
        self,
        ctx: Any,
        *,
        tool_name: str,
        action: str,
        args: dict,
        status: str,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        """每一次工具调用（读/写/本地）的落点，含 deny/cancel/failed。"""

    async def on_assistant_output(self, ctx: Any, text: str) -> None:
        """最终返回给用户的结果落点。"""


class MiddlewareChain:
    """按声明顺序串起多个切面；redact_* 依次透传，on_* 依次广播。"""

    def __init__(self, middlewares: list[Middleware] | None = None) -> None:
        self._mws: list[Middleware] = list(middlewares or [])

    def redact_input(self, text: str) -> str:
        for mw in self._mws:
            text = mw.redact_input(text)
        return text

    def redact_output(self, text: str) -> str:
        for mw in self._mws:
            text = mw.redact_output(text)
        return text

    async def on_user_input(self, ctx: Any, text: str) -> None:
        for mw in self._mws:
            await mw.on_user_input(ctx, text)

    async def on_tool_call(self, ctx: Any, **kwargs: Any) -> None:
        for mw in self._mws:
            await mw.on_tool_call(ctx, **kwargs)

    async def on_assistant_output(self, ctx: Any, text: str) -> None:
        for mw in self._mws:
            await mw.on_assistant_output(ctx, text)
