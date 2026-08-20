from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from .engine import STAGES, Engine
from .models import Run
from .session_ctx import bind_db, current_db


class AgentState(TypedDict, total=False):
    run_id: str
    pile_id: str
    stage: str
    status: str
    empty_pile: bool
    kill: bool
    path_decisions: list[dict[str, Any]]


def _payload(run: Run) -> dict[str, Any]:
    return json.loads(run.state_json or "{}")


def _run_until(stage: str, state: AgentState) -> AgentState:
    db = current_db()
    run = db.get(Run, state["run_id"])
    if not run:
        return {**state, "status": "missing"}
    Engine(db).advance(run, stop_after=stage, pause_on_stop=False)
    db.refresh(run)
    payload = _payload(run)
    empty = bool(payload.get("empty_pile"))
    paused = run.status == "paused"
    return {
        **state,
        "stage": run.current_stage,
        "status": run.status,
        "kill": paused,
        "empty_pile": empty,
        "path_decisions": payload.get("path_decisions") or [],
    }


def _route_after(state: AgentState) -> Literal["paused", "empty_gate", "continue"]:
    if state.get("kill") or state.get("status") == "paused":
        return "paused"
    if state.get("empty_pile"):
        return "empty_gate"
    return "continue"


def build_graph():
    g = StateGraph(AgentState)

    def make_node(stage_name: str):
        def node(state: AgentState) -> AgentState:
            return _run_until(stage_name, state)

        node.__name__ = stage_name
        return node

    for stage in STAGES:
        g.add_node(stage, make_node(stage))

    g.set_entry_point("ingest")
    g.add_conditional_edges(
        "ingest",
        _route_after,
        {"paused": END, "empty_gate": "gate", "continue": "classify"},
    )
    chain = ["classify", "extract", "reconcile", "draft", "examine"]
    for a, b in zip(chain, chain[1:] + ["gate"]):
        g.add_conditional_edges(
            a,
            _route_after,
            {"paused": END, "empty_gate": "gate", "continue": b},
        )
    g.add_edge("gate", END)
    return g.compile()


graph = build_graph()


def invoke_run(db: Session, run: Run) -> AgentState:
    with bind_db(db):
        return graph.invoke({"run_id": run.id, "pile_id": run.pile_id, "path_decisions": []})
