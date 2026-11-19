"""The contact workflow as a LangGraph state machine.

Shape: ``drain_inbox → supervisor → <one specialist> → supervisor → … → await_event``.
The supervisor is the only node with outgoing choices and it chooses with ``decide_route``, a pure
function of state. ``await_event`` is the single interrupt; the thread sleeps there, checkpointed
in PostgreSQL, until the worker resumes it with a bare wake signal. What wakes it is always an
inbox event: a customer message, an outage declared or resolved on the customer's node, or a
timer the workflow armed for itself.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from skylink_care.observability.tracing import keep_trace_contents_private
from skylink_care.orchestrator.nodes import Nodes
from skylink_care.orchestrator.state import ContactState, Route
from skylink_care.runtime import OrchestratorRuntime

# Per-attempt wall-clock limit for a specialist node, and how often a failed node is retried.
NODE_TIMEOUT_SECONDS = 30.0
NODE_RETRY = RetryPolicy(max_attempts=2)
# Upper bound on graph supersteps in one run; the supervisor's own step guard trips well before.
RECURSION_LIMIT = 60

type ContactGraph = CompiledStateGraph[ContactState, Any, Any, Any]

_SPECIALISTS = (
    Route.TRIAGE,
    Route.ASK_IDENTITY,
    Route.OUT_OF_SCOPE,
    Route.DIAGNOSE,
    Route.OUTAGE_REPLY,
    Route.UNKNOWN_REPLY,
    Route.CLEAR_REPLY,
    Route.LINE_HOLD,
    Route.GUIDE,
    Route.RESTORED,
    Route.KEEP_WAITING,
    Route.CREDIT_RESULT,
    Route.VISIT,
    Route.VISIT_RESULT,
    Route.CLOSE,
    Route.HANDOFF,
)


def build_graph(
    runtime: OrchestratorRuntime, checkpointer: BaseCheckpointSaver[Any]
) -> ContactGraph:
    # Whoever builds the graph may have LangSmith tracing on: its traces carry no contents
    # unless a setting asks for them.
    keep_trace_contents_private()
    nodes = Nodes(runtime)
    graph = StateGraph(ContactState)

    graph.add_node("drain_inbox", nodes.drain_inbox)
    graph.add_node("supervisor", nodes.supervisor)
    graph.add_node(Route.AWAIT.value, nodes.await_event)
    graph.add_node(Route.FINISH.value, nodes.finish)
    for route in _SPECIALISTS:
        graph.add_node(
            route.value,
            getattr(nodes, route.value),
            timeout=NODE_TIMEOUT_SECONDS,
            retry_policy=NODE_RETRY,
        )
        graph.add_edge(route.value, "supervisor")

    graph.add_edge(START, "drain_inbox")
    graph.add_edge("drain_inbox", "supervisor")
    graph.add_conditional_edges(
        "supervisor", _chosen_route, {route.value: route.value for route in Route}
    )
    graph.add_edge(Route.AWAIT.value, "drain_inbox")
    graph.add_edge(Route.FINISH.value, END)
    return graph.compile(checkpointer=checkpointer)


def _chosen_route(state: ContactState) -> str:
    return state["route"]
