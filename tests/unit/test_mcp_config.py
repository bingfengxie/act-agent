import pytest

from act_agent.mcp.config import normalize_server, parse_config


def test_claude_style_http_config():
    cfg = parse_config(
        {"mcpServers": {"act-mcp": {"type": "http", "url": "http://localhost:8090/mcp"}}}
    )
    assert cfg["act-mcp"] == {
        "transport": "streamable_http",
        "url": "http://localhost:8090/mcp",
    }


def test_headers_and_sse_and_stdio():
    assert normalize_server("s", {"type": "http", "url": "u", "headers": {"A": "b"}})[
        "headers"
    ] == {"A": "b"}
    assert normalize_server("s", {"type": "sse", "url": "u"})["transport"] == "sse"
    stdio = normalize_server("s", {"type": "stdio", "command": "python", "args": ["-m", "x"]})
    assert stdio == {"transport": "stdio", "command": "python", "args": ["-m", "x"]}


def test_already_adapted_config_passthrough():
    raw = {"transport": "streamable_http", "url": "http://x"}
    assert normalize_server("s", raw) == raw


def test_invalid_configs():
    with pytest.raises(ValueError):
        normalize_server("s", {"type": "http"})
    with pytest.raises(ValueError):
        normalize_server("s", {"type": "carrier-pigeon", "url": "u"})
