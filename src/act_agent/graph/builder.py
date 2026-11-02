from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from act_agent.graph.nodes import (
    agent_node,
    collect_node,
    prepare_node,
    route_node,
    should_continue,
    tools_node,
)
from act_agent.graph.state import AgentState


def build_graph(checkpointer):
    graph = StateGraph(AgentState)
    graph.add_node("prepare", prepare_node)
    graph.add_node("route", route_node)
    graph.add_node("collect", collect_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "route")
    graph.add_edge("route", "collect")
    graph.add_edge("collect", "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
