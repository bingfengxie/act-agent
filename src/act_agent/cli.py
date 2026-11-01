"""Minimal interactive client for the HTTP API (SSE aware)."""

from __future__ import annotations

import argparse
import json
import select
import sys

import httpx

try:  # prompt_toolkit 正确处理中文宽字符的退格/光标；macOS 自带 libedit 做不到
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
except ImportError:  # pragma: no cover - 仅回退到基础输入
    PromptSession = None  # type: ignore[assignment]
    KeyBindings = None  # type: ignore[assignment]

PASTE_MARKERS = {"/paste", ":paste"}
PASTE_END = ">>>"
_session = None


def _has_pending_stdin(timeout: float = 0.05) -> bool:
    """终端里粘贴多行时，第一行之后的数据通常还在内核缓冲区里。"""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(ready)
    except (OSError, ValueError):
        return False


def _flush_pending_input(timeout: float = 0.1) -> None:
    """丢弃粘贴残留在缓冲区里的行，避免它被当成确认语提交。"""
    while _has_pending_stdin(timeout):
        if not sys.stdin.readline():
            break


def _read_paste_block() -> str:
    print(f"粘贴模式：粘贴完内容后，单独一行输入 {PASTE_END} 提交")
    lines: list[str] = []
    for line in sys.stdin:
        if line.strip() == PASTE_END:
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines)


def _build_session():
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event):  # Enter 提交整段
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event):  # Alt/Option+Enter 手动换行
        event.current_buffer.insert_text("\n")

    return PromptSession(multiline=True, key_bindings=bindings)


def _read_with_session(prompt: str) -> str | None:
    global _session
    if _session is None:
        _session = _build_session()
    try:
        text = _session.prompt(prompt)
        if text.strip() in PASTE_MARKERS:
            text = _session.prompt("粘贴内容（Enter 提交）\n> ")
        return text
    except (EOFError, KeyboardInterrupt):
        return None


def _read_message_fallback(prompt: str = "\n> ") -> str | None:
    """非 TTY / 无 prompt_toolkit 时：逐行读取并把粘贴的剩余行合并进来。"""
    try:
        first = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    if first.strip() in PASTE_MARKERS:
        return _read_paste_block()

    lines = [first]
    # 粘贴的剩余行已经在输入缓冲区里，一次性读走，否则会被 shell 当成命令
    while _has_pending_stdin():
        line = sys.stdin.readline()
        if not line:
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines)


def read_message(prompt: str = "\n> ") -> str | None:
    """交互终端用 prompt_toolkit（中文退格正常、支持粘贴多行）；否则走基础读取。"""
    if PromptSession is not None and sys.stdin.isatty() and sys.stdout.isatty():
        return _read_with_session(prompt)
    return _read_message_fallback(prompt)


def _stream(client: httpx.Client, url: str, payload: dict, token: str) -> str | None:
    """Print a turn; return the interrupt confirm prompt if the graph paused."""
    pending: str | None = None
    with client.stream(
        "POST", url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=None
    ) as resp:
        if resp.status_code >= 400:
            print(f"HTTP {resp.status_code}: {resp.read().decode()}", file=sys.stderr)
            return None
        for event, data in _iter_events(resp.iter_lines()):
            if event == "token":
                sys.stdout.write(json.loads(data).get("text", ""))
                sys.stdout.flush()
            elif event == "message":
                print(json.loads(data).get("content", ""))
            elif event == "interrupt":
                payload_data = json.loads(data).get("payload", {})
                print("\n" + payload_data.get("markdown", ""))
                pending = payload_data.get("confirm_prompt")
            elif event == "error":
                print(f"\n[错误] {json.loads(data).get('message')}", file=sys.stderr)
    print()
    return pending


def _iter_events(lines):
    event, data = None, []
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].strip())
        elif line == "" and event:
            yield event, "\n".join(data)
            event, data = None, []
    if event:
        yield event, "\n".join(data)


def main() -> None:
    parser = argparse.ArgumentParser(prog="act-agent")
    parser.add_argument("--base", default="http://127.0.0.1:8088")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--username", default="operator")
    parser.add_argument("--password", default="operator123")
    parser.add_argument("--session", default=None, help="复用已有 session id")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base, timeout=30) as client:
        resp = client.post(
            "/v1/auth/login",
            json={"tenant": args.tenant, "username": args.username, "password": args.password},
        )
        if resp.status_code != 200:
            print(f"登录失败: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
        token = resp.json()["access_token"]
        print(f"已登录 {args.username}@{args.tenant}")

        session_id = args.session
        if not session_id:
            created = client.post(
                "/v1/sessions", headers={"Authorization": f"Bearer {token}"}, json={}
            )
            created.raise_for_status()
            session_id = created.json()["id"]
        print(f"session: {session_id}（输入 /quit 退出）")

        print("提示：可直接粘贴多行内容；Enter 发送，Option/Alt+Enter 换行")
        pending: str | None = None
        while True:
            raw = read_message()
            if raw is None:
                break
            text = raw.strip()
            if text in {"/quit", "/exit"}:
                break
            if not text:
                # 空回车不能当成确认/取消提交；有未决确认时继续等待
                if pending:
                    print("（未提交：请输入确认语，例如 确认创建 <名称>，或 取消）")
                    continue
                break
            if pending:
                pending = _stream(
                    client,
                    f"/v1/sessions/{session_id}/resume",
                    {"answer": text},
                    token,
                )
                if pending is None:
                    pending = None
            else:
                pending = _stream(
                    client,
                    f"/v1/sessions/{session_id}/messages",
                    {"message": text},
                    token,
                )
            if pending:
                _flush_pending_input()
                print("\n（等待确认：直接回复上面的确认语，或回复 取消）")


if __name__ == "__main__":
    main()
