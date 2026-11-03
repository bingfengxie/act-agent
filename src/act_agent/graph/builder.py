from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from act_agent.graph.nodes import (
    agent_node,
    collect_node,
    compact_node,
    route_node,
    should_continue,
    tools_node,
)
from act_agent.graph.state import AgentState


def build_graph(checkpointer):
    graph = StateGraph(AgentState)
    graph.add_node("route", route_node)
    graph.add_node("collect", collect_node)
    graph.add_node("compact", compact_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

    graph.add_edge(START, "route")
    graph.add_edge("route", "collect")
    graph.add_edge("collect", "compact")
    graph.add_edge("compact", "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    # tools 之后重新结算：轮内工具结果膨胀也会被压掉；HITL resume 也走这条边
    graph.add_edge("tools", "compact")
    return graph.compile(checkpointer=checkpointer)
