"""MCP adapter for StockFlow.

This module intentionally keeps MCP as an integration layer over the existing
FastAPI/LangGraph simulator. It implements the JSON-RPC methods needed by MCP
clients without pulling a runtime dependency into the deployed demo.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from agents.demo_simulator import (
    apply_scenario,
    approve_decision,
    demo_impact_metrics,
    ensure_demo_schema,
    get_agent_events,
    get_demo_state,
    get_pending_decisions,
    get_reasoning_traces,
    reject_decision,
    reset_demo,
    run_demo_tick,
)
from agents.live_signals import get_live_signal_summary
from data.db import SessionLocal


PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "stockflow-mcp", "version": "0.1.0"}


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Session, dict[str, Any]], Any]
    destructive: bool = False

    def list_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_demo_state(db: Session, args: dict[str, Any]) -> Any:
    return get_demo_state(db)


def _get_pending_decisions(db: Session, args: dict[str, Any]) -> Any:
    return get_pending_decisions(db)[: _as_int(args.get("limit"), 20)]


def _get_agent_events(db: Session, args: dict[str, Any]) -> Any:
    return get_agent_events(db, limit=_as_int(args.get("limit"), 30))


def _get_reasoning_traces(db: Session, args: dict[str, Any]) -> Any:
    return get_reasoning_traces(db, limit=_as_int(args.get("limit"), 30))


def _get_demo_metrics(db: Session, args: dict[str, Any]) -> Any:
    return demo_impact_metrics(db)


def _run_simulation_tick(db: Session, args: dict[str, Any]) -> Any:
    return run_demo_tick(db)


def _set_scenario(db: Session, args: dict[str, Any]) -> Any:
    scenario_name = str(args.get("scenario_name", "")).strip()
    if not scenario_name:
        raise ValueError("scenario_name is required")
    return apply_scenario(db, scenario_name)


def _approve_decision(db: Session, args: dict[str, Any]) -> Any:
    decision_id = _as_int(args.get("decision_id"), 0)
    if decision_id <= 0:
        raise ValueError("decision_id must be a positive integer")
    return approve_decision(db, decision_id)


def _reject_decision(db: Session, args: dict[str, Any]) -> Any:
    decision_id = _as_int(args.get("decision_id"), 0)
    if decision_id <= 0:
        raise ValueError("decision_id must be a positive integer")
    return reject_decision(db, decision_id)


def _reset_demo(db: Session, args: dict[str, Any]) -> Any:
    return reset_demo(db)


def _get_live_signals(db: Session, args: dict[str, Any]) -> Any:
    return get_live_signal_summary(db, force_refresh=bool(args.get("force_refresh", False)))


LIMIT_SCHEMA = {
    "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "description": "Maximum number of records to return.",
        "default": 30,
    }
}


TOOLS: dict[str, McpTool] = {
    tool.name: tool
    for tool in [
        McpTool(
            "get_demo_state",
            "Return the full StockFlow simulator state for the live franchise network.",
            _object_schema(),
            _get_demo_state,
        ),
        McpTool(
            "get_pending_decisions",
            "Return human approval decisions proposed by StockFlow agents.",
            _object_schema(LIMIT_SCHEMA),
            _get_pending_decisions,
        ),
        McpTool(
            "get_agent_events",
            "Return recent agent timeline events.",
            _object_schema(LIMIT_SCHEMA),
            _get_agent_events,
        ),
        McpTool(
            "get_reasoning_traces",
            "Return LangGraph-style tool-call traces with observations and decisions.",
            _object_schema(LIMIT_SCHEMA),
            _get_reasoning_traces,
        ),
        McpTool(
            "get_demo_metrics",
            "Return baseline-vs-agent impact metrics.",
            _object_schema(),
            _get_demo_metrics,
        ),
        McpTool(
            "get_live_signals",
            "Return weather and holiday signals used by the demand forecast agent.",
            _object_schema(
                {
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Refresh public API signals instead of using the cache.",
                        "default": False,
                    }
                }
            ),
            _get_live_signals,
        ),
        McpTool(
            "run_simulation_tick",
            "Advance the synthetic franchise network by one day and run all agents.",
            _object_schema(),
            _run_simulation_tick,
            destructive=True,
        ),
        McpTool(
            "set_scenario",
            "Load a scenario such as weekend-rush, game-day-spike, delivery-delay, expiry-rescue, or store-to-store-transfer.",
            _object_schema(
                {
                    "scenario_name": {
                        "type": "string",
                        "description": "Scenario slug.",
                        "enum": [
                            "weekend-rush",
                            "game-day-spike",
                            "delivery-delay",
                            "expiry-rescue",
                            "store-to-store-transfer",
                        ],
                    }
                },
                ["scenario_name"],
            ),
            _set_scenario,
            destructive=True,
        ),
        McpTool(
            "approve_decision",
            "Approve a pending synthetic agent decision idempotently.",
            _object_schema(
                {"decision_id": {"type": "integer", "minimum": 1, "description": "Agent decision id."}},
                ["decision_id"],
            ),
            _approve_decision,
            destructive=True,
        ),
        McpTool(
            "reject_decision",
            "Reject a pending synthetic agent decision idempotently.",
            _object_schema(
                {"decision_id": {"type": "integer", "minimum": 1, "description": "Agent decision id."}},
                ["decision_id"],
            ),
            _reject_decision,
            destructive=True,
        ),
        McpTool(
            "reset_demo",
            "Reset the synthetic simulator state, events, decisions, and inventory baseline.",
            _object_schema(),
            _reset_demo,
            destructive=True,
        ),
    ]
}


RESOURCES = [
    {
        "uri": "stockflow://current-state",
        "name": "Current simulator state",
        "description": "Restaurants, warehouses, metrics, events, and pending decisions.",
        "mimeType": "application/json",
    },
    {
        "uri": "stockflow://metrics/demo-impact",
        "name": "Demo impact metrics",
        "description": "Without-agents vs with-agents proof metrics.",
        "mimeType": "application/json",
    },
    {
        "uri": "stockflow://agents/reasoning-traces",
        "name": "Agent reasoning traces",
        "description": "Tool calls, observations, and decisions from the agent graph.",
        "mimeType": "application/json",
    },
    {
        "uri": "stockflow://architecture",
        "name": "StockFlow architecture",
        "description": "Plain-English architecture summary for AI clients.",
        "mimeType": "text/plain",
    },
]


PROMPTS = [
    {
        "name": "explain_franchise_risk",
        "description": "Explain current stockout and waste risk for a franchise operator.",
        "arguments": [],
    },
    {
        "name": "compare_agents_vs_baseline",
        "description": "Compare StockFlow agent decisions against the no-agent baseline.",
        "arguments": [],
    },
    {
        "name": "prepare_recruiter_demo_script",
        "description": "Create a concise walkthrough script for a recruiter or hiring manager.",
        "arguments": [],
    },
]


def handle_mcp_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC MCP message."""
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "notifications/initialized":
        return None

    try:
        result = _dispatch(method, params)
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except ValueError as exc:
        if request_id is None:
            return None
        return _error(request_id, -32602, str(exc))
    except KeyError as exc:
        if request_id is None:
            return None
        return _error(request_id, -32601, str(exc))
    except Exception as exc:
        if request_id is None:
            return None
        return _error(request_id, -32000, str(exc))


def _dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": SERVER_INFO,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [tool.list_payload() for tool in TOOLS.values()]}
    if method == "tools/call":
        return _call_tool(params)
    if method == "resources/list":
        return {"resources": RESOURCES}
    if method == "resources/read":
        return _read_resource(params)
    if method == "prompts/list":
        return {"prompts": PROMPTS}
    if method == "prompts/get":
        return _get_prompt(params)
    raise KeyError(f"Unsupported MCP method: {method}")


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool: {name}")

    db = SessionLocal()
    try:
        ensure_demo_schema(db)
        result = tool.handler(db, arguments)
        if tool.destructive:
            db.commit()
        return _tool_text(result)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _read_resource(params: dict[str, Any]) -> dict[str, Any]:
    uri = params.get("uri")
    db = SessionLocal()
    try:
        ensure_demo_schema(db)
        if uri == "stockflow://current-state":
            payload = get_demo_state(db)
            mime_type = "application/json"
        elif uri == "stockflow://metrics/demo-impact":
            payload = demo_impact_metrics(db)
            mime_type = "application/json"
        elif uri == "stockflow://agents/reasoning-traces":
            payload = get_reasoning_traces(db, limit=50)
            mime_type = "application/json"
        elif uri == "stockflow://architecture":
            payload = (
                "StockFlow uses FastAPI for normal app integration, LangGraph-style "
                "agent orchestration for supply-chain decisions, Postgres/PostGIS for "
                "durable state and geospatial transfer reasoning, and MCP as an AI "
                "client integration adapter over the same tools."
            )
            mime_type = "text/plain"
        else:
            raise ValueError(f"Unknown resource URI: {uri}")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": mime_type,
                    "text": _serialize(payload) if mime_type == "application/json" else str(payload),
                }
            ]
        }
    finally:
        db.close()


def _get_prompt(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    prompts = {
        "explain_franchise_risk": (
            "Use StockFlow MCP tools to inspect current state, pending decisions, "
            "and metrics. Explain which stores are at stockout or expiry risk, "
            "which agent proposed each action, and what a manager should approve first."
        ),
        "compare_agents_vs_baseline": (
            "Use get_demo_metrics and get_reasoning_traces to compare the no-agent "
            "baseline against StockFlow's agent decisions. Focus on stockouts, waste, "
            "fill rate, transfers, order quantity, and profit saved."
        ),
        "prepare_recruiter_demo_script": (
            "Create a 90-second recruiter walkthrough of StockFlow. Explain the "
            "problem, the LangGraph multi-agent flow, the MCP integration, the live "
            "signals, and the measurable business impact."
        ),
    }
    prompt = prompts.get(name)
    if prompt is None:
        raise ValueError(f"Unknown prompt: {name}")
    return {
        "description": next((p["description"] for p in PROMPTS if p["name"] == name), name),
        "messages": [{"role": "user", "content": {"type": "text", "text": prompt}}],
    }


def _tool_text(payload: Any, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": _serialize(payload)}], "isError": is_error}


def _serialize(payload: Any) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def run_stdio() -> None:
    """Run a newline-delimited JSON-RPC MCP server over stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle_mcp_message(message)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio()
