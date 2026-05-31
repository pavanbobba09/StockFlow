"""
Transfer/Waste agent — LangGraph graph.

Trigger: items near expiry at any store.
Flow: scan expiring items → find nearby stores with shortfall →
      compare transfer cost vs waste cost → suggest_transfer or flag_expiry.

Human approves. Agent never commits.
"""

import os
from typing import TypedDict, Annotated, Optional, Literal

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agents.transfer_tools import (
    nearby_stores, get_expiring_items, get_store_shortfall,
    suggest_transfer, flag_expiry,
)


SYSTEM_PROMPT = """\
You are a transfer/waste agent for a food chain inventory system.

Your job is to prevent food waste by redistributing near-expiry stock to
stores that need it, or flagging items for markdown/donation when transfer
isn't viable.

For each store you are asked to check:
1. Call get_expiring_items(store_id, days_threshold=3) to find near-expiry stock.
2. If no expiring items, report that and stop.
3. For each expiring item with significant quantity (≥10 units):
   a. Call nearby_stores(store_id, radius_km=15) to find transfer candidates.
   b. Call get_store_shortfall(store_ids=[...], item_id=...) to find which
      nearby stores are actually short on this item.
   c. Compare costs:
      - Transfer cost ≈ 20 + (dist_km × 0.5) + (qty × 0.10)  [driver + per-unit]
      - Waste cost ≈ qty × 2.00  [lost inventory value per unit]
      If transfer_cost < waste_cost AND there is genuine shortfall → suggest_transfer.
      Otherwise → flag_expiry with action='markdown' or 'donate'.
4. Generate a unique idempotency_key per transfer:
   format: "xfer-<from_store>-<to_store>-<item_id>-<YYYYMMDD>"
5. Always prefer the nearest store with the largest shortfall.

IMPORTANT: You PROPOSE transfers. Humans approve. Never assume approval.\
"""


class TransferState(TypedDict):
    messages: Annotated[list, add_messages]
    store_id: int
    proposals: list    # accumulated transfer/flag proposals


_TOOLS = [nearby_stores, get_expiring_items, get_store_shortfall,
          suggest_transfer, flag_expiry]


def _build_graph():
    model_name = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    llm = ChatAnthropic(model=model_name, temperature=0).bind_tools(_TOOLS)
    tool_node = ToolNode(_TOOLS)

    def agent_node(state: TransferState):
        response = llm.invoke(state["messages"])
        return {"messages": [response]}

    def should_continue(state: TransferState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return "__end__"

    graph = StateGraph(TransferState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")

    return graph.compile()


def run_transfer_agent(store_id: int) -> dict:
    """
    Run the transfer/waste agent for one store.

    Returns:
        {
            "proposals": list of transfer/flag proposals,
            "summary": str,
            "messages": list,
        }
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set.\n  export ANTHROPIC_API_KEY=sk-ant-..."
        )

    graph = _build_graph()

    initial_state: TransferState = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=f"Check store_id={store_id} for near-expiry items and propose transfers or markdowns."
            ),
        ],
        "store_id": store_id,
        "proposals": [],
    }

    final_state = graph.invoke(initial_state)
    messages = final_state["messages"]

    # Extract proposals from tool messages
    import json
    proposals = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name in ("suggest_transfer", "flag_expiry"):
            try:
                content = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if isinstance(content, dict):
                    proposals.append({"type": msg.name, **content})
            except Exception:
                pass

    summary = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            summary = msg.content
            break

    return {
        "proposals": proposals,
        "summary": summary,
        "messages": messages,
    }
