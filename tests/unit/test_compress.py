from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from act_agent.graph.compress import ContextCompressor
from act_agent.settings import Settings
from act_agent.utils.tokens import estimate_tokens


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return AIMessage(content="摘要：用户要创建中秋节3折扣活动，预算1212元，已预览待确认。")

    async def bind_tools(self, tools):
        return self


def test_token_estimate_handles_cjk():
    assert estimate_tokens("中秋节活动") > estimate_tokens("abc")
    assert estimate_tokens("") == 0


async def test_no_compression_under_threshold():
    settings = Settings(max_context_tokens=100_000, keep_recent_messages=20)
    compressor = ContextCompressor(_FakeLLM(), settings)
    messages = [HumanMessage(content="你好")] * 3
    summary, split = await compressor.prepare(messages, "旧摘要", 0)
    assert summary == "旧摘要"
    assert split == 0


async def test_compression_produces_summary_and_split():
    settings = Settings(max_context_tokens=120, keep_recent_messages=2)
    llm = _FakeLLM()
    compressor = ContextCompressor(llm, settings)
    messages = [
        HumanMessage(content=f"第{i}条消息，讲的是中秋节活动的预算和商品折扣细节。")
        for i in range(8)
    ]
    summary, split = await compressor.prepare(messages, "", 0)
    assert llm.calls == 1
    assert "中秋节" in summary
    assert 0 < split < len(messages)


async def test_split_never_starts_with_tool_message():
    settings = Settings(max_context_tokens=10, keep_recent_messages=2)
    compressor = ContextCompressor(_FakeLLM(), settings)
    messages = [
        HumanMessage(content="创建活动"),
        AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "c1"}]),
        ToolMessage(content="ok", name="x", tool_call_id="c1"),
        ToolMessage(content="ok2", name="x", tool_call_id="c1"),
        HumanMessage(content="继续"),
    ]
    split = compressor._split_index(messages)
    assert not isinstance(messages[split], ToolMessage)


async def test_fallback_without_llm_keeps_previous_summary():
    settings = Settings(max_context_tokens=50, keep_recent_messages=1)
    compressor = ContextCompressor(None, settings)
    messages = [HumanMessage(content="很长的一段对话内容需要被压缩掉。") for _ in range(6)]
    summary, split = await compressor.prepare(messages, "已有摘要", 0)
    assert "已有摘要" in summary
    assert split > 0
