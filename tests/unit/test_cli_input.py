import io

import act_agent.cli as cli


def _input(value):
    def _fake(prompt=""):
        if isinstance(value, BaseException):
            raise value
        return value

    return _fake


def test_single_line(monkeypatch):
    monkeypatch.setattr("builtins.input", _input("你好"))
    monkeypatch.setattr(cli, "_has_pending_stdin", lambda timeout=0.05: False)
    assert cli.read_message("> ") == "你好"


def test_multiline_paste_is_read_as_one_message(monkeypatch):
    monkeypatch.setattr("builtins.input", _input("一次性创建活动"))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("活动名称：中秋节6\n门店ID：10001\n"))
    pending = iter([True, True, False])
    monkeypatch.setattr(cli, "_has_pending_stdin", lambda timeout=0.05: next(pending))

    assert cli.read_message("> ") == "一次性创建活动\n活动名称：中秋节6\n门店ID：10001"


def test_explicit_paste_mode(monkeypatch):
    monkeypatch.setattr("builtins.input", _input("/paste"))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("第一行\n第二行\n>>>\n不该被读到\n"))
    assert cli.read_message("> ") == "第一行\n第二行"


def test_eof_returns_none(monkeypatch):
    monkeypatch.setattr("builtins.input", _input(EOFError()))
    assert cli.read_message("> ") is None
