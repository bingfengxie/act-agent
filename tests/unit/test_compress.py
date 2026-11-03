from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

from act_agent.auth.rbac import Principal
from act_agent.graph.compress import ContextCompressor
from act_agent.graph.context import RunContext
from act_agent.graph.nodes import compact_node
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


def _tool_round(call_id: str, text: str) -> list:
    return [
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": call_id}]),
        ToolMessage(content=text, name="read", tool_call_id=call_id),
    ]


def test_token_estimate_handles_cjk():
    assert estimate_tokens("中秋节活动") > estimate_tokens("abc")
    assert estimate_tokens("") == 0


async def test_no_compaction_under_threshold():
    settings = Settings(max_context_tokens=100_000, keep_recent_messages=20)
    compressor = ContextCompressor(_FakeLLM(), settings)
    messages = [HumanMessage(content="你好")] * 3
    summary, split = await compressor.compact(messages, "旧摘要")
    assert summary == "旧摘要"
    assert split == 0


async def test_compaction_produces_summary_and_split():
    settings = Settings(max_context_tokens=120, keep_recent_messages=2)
    llm = _FakeLLM()
    compressor = ContextCompressor(llm, settings)
    messages = [
        HumanMessage(content=f"第{i}条消息，讲的是中秋节活动的预算和商品折扣细节。")
        for i in range(8)
    ]
    summary, split = await compressor.compact(messages, "")
    assert llm.calls == 1
    assert "中秋节" in summary
    assert 0 < split <= len(messages) - 1  # 当前轮（最后一条）必须保留


async def test_split_never_starts_with_tool_message():
    settings = Settings(max_context_tokens=10, keep_recent_messages=2)
    compressor = ContextCompressor(_FakeLLM(), settings)
    messages = [
        HumanMessage(content="更早"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "read", "args": {}, "id": "c1"},
                {"name": "read", "args": {}, "id": "c2"},
            ],
        ),
        ToolMessage(content="r1", name="read", tool_call_id="c1"),
        ToolMessage(content="r2", name="read", tool_call_id="c2"),
        *_tool_round("c3", "r3"),
        HumanMessage(content="本轮"),
    ]
    # n - keep = 5，正好落在 ToolMessage 上；切分点必须跳过孤儿工具结果
    split = compressor._split_index(messages)
    assert not isinstance(messages[split], ToolMessage)
    assert isinstance(messages[split], HumanMessage)


async def test_compaction_never_drops_current_turn():
    settings = Settings(max_context_tokens=10, compress_ratio=0.1, keep_recent_messages=1)
    compressor = ContextCompressor(_FakeLLM(), settings)
    messages = [
        HumanMessage(content=f"历史{i}")
        for i in range(6)
    ] + [HumanMessage(content="本轮请求"), *_tool_round("c1", "结果")]
    split = compressor._split_index(messages)
    assert split <= 6  # 最后一条 HumanMessage 的下标


async def test_token_budget_trims_older_turns_beyond_count_cap():
    settings = Settings(max_context_tokens=60, compress_ratio=0.2, keep_recent_messages=5)
    compressor = ContextCompressor(_FakeLLM(), settings)
    old = [HumanMessage(content="很长的一段历史对话内容，需要被压掉。") for _ in range(8)]
    messages = old + [HumanMessage(content="本轮请求"), AIMessage(content="处理中")]
    split = compressor._split_index(messages)
    assert 6 < split <= 8  # 越过条数上限后继续按 token 预算裁掉旧轮次


async def test_fallback_without_llm_keeps_previous_summary():
    settings = Settings(max_context_tokens=50, keep_recent_messages=1)
    compressor = ContextCompressor(None, settings)
    messages = [HumanMessage(content="很长的一段对话内容需要被压缩掉。") for _ in range(6)]
    summary, split = await compressor.compact(messages, "已有摘要")
    assert "已有摘要" in summary
    assert split > 0


def test_fit_view_truncates_tool_output_without_mutating_state():
    settings = Settings(max_context_tokens=100)
    compressor = ContextCompressor(None, settings)
    big = "很长的工具结果" * 200
    msg = ToolMessage(content=big, name="read", tool_call_id="c1")
    messages = [HumanMessage(content="查询"), *_tool_round("c1", big)[:-1], msg]

    view = compressor.fit_view(messages)

    assert len(view[-1].content) < len(big)
    assert "省略" in view[-1].content
    assert messages[-1].content == big  # 原始 state 不被修改


async def test_compact_node_drops_prefix_and_rebases_collected_cursor():
    tiny = Settings(max_context_tokens=40, compress_ratio=0.5, keep_recent_messages=1)
    ctx = RunContext(
        principal=Principal(user_id="u", tenant_id="t", roles=(), scopes=frozenset()),
        tools=[],
        registry=None,
        router=None,
        compressor=ContextCompressor(None, tiny),
        llm=None,
        settings=tiny,
        session_id="s",
        thread_id="th",
    )
    messages = add_messages(
        [],
        [HumanMessage(content=f"旧消息{i}，内容足够长以超过阈值") for i in range(10)]
        + [HumanMessage(content="本轮请求")],
    )
    state = {
        "messages": messages,
        "summary": "",
        "task_state": {"collected_upto": len(messages), "draft": {"name": "中秋节"}},
    }

    out = await compact_node(state, {"configurable": {"ctx": ctx}})

    split = len(messages) - (len(out["messages"]) - 1)
    assert split == len(messages) - 1  # 只保留当前轮
    merged = add_messages(messages, out["messages"])
    assert len(merged) == 1
    assert merged[0].content == "本轮请求"
    assert out["task_state"]["collected_upto"] == len(messages) - split
    assert out["task_state"]["draft"] == {"name": "中秋节"}
    assert out["summary"]
