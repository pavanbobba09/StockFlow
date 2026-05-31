"""
Replenishment agent — LangGraph graph.

Flow:
  START → agent → tools → agent → ... → END
                                    ↑
                          stops after draft_order is called

The agent gathers context (stock, forecast, par), reasons about the
ordering decision, and calls draft_order as its final action.
Humans approve; place_order is called separately (see demo.py).
"""

import os
from typing import TypedDict, Annotated, Optional, Literal

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agents.tools import get_stock, get_forecast, get_par_levels, draft_order


SYSTEM_PROMPT = """\
You are a replenishment agent for a food chain inventory management system.

Your task for each run:
1. Call get_stock(store_id, item_id) to check current inventory and expiry.
2. Call get_par_levels(store_id, item_id) to get the target level and shelf life.
3. Call get_forecast(store_id, item_id, horizon=<delivery_gap_days>) to predict demand.
4. Reason: will we stock out before the next delivery? How much to order?
   - Order = par - (current_qty - forecast_total), capped by shelf_life constraints.
   - If current stock covers forecast demand + safety stock, no order needed.
   - Never order more than can sell before shelf life expires.
5. If an order is needed, call draft_order with:
   - store_id, item_id, quantity (integer)
   - idempotency_key: a unique string you generate (format: "repl-<store_id>-<item_id>-<YYYYMMDD>")
   - reason: one sentence explaining the decision
6. If no order is needed, state why clearly and stop.

IMPORTANT: You PROPOSE orders. Humans approve. Do not assume approval.\
"""


class ReplenishmentState(TypedDict):
    messages: Annotated[list, add_messages]
    store_id: int
    item_id: int
    draft_order_id: Optional[int]


_TOOLS = [get_stock, get_forecast, get_par_levels, draft_order]


def _build_graph():
    model_name = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    llm = ChatAnthropic(model=model_name, temperature=0).bind_tools(_TOOLS)
    tool_node = ToolNode(_TOOLS)

    def agent_node(state: ReplenishmentState):
        response = llm.invoke(state["messages"])
        # Check if draft_order was called — extract order_id from next tool run
        return {"messages": [response]}

    def should_continue(state: ReplenishmentState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if not hasattr(last, "tool_calls") or not last.tool_calls:
            return "__end__"
        # If draft_order is among the calls, execute it then stop
        return "tools"

    def after_tools(state: ReplenishmentState) -> Literal["agent", "__end__"]:
        """After executing tools, check if draft_order just ran — stop if so."""
        for msg in reversed(state["messages"]):
            if isinstance(msg, ToolMessage) and msg.name == "draft_order":
                # Extract order_id for the caller
                import json
                try:
                    content = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                    if isinstance(content, dict) and "order_id" in content:
                        return "__end__"
                except Exception:
                    pass
        return "agent"

    graph = StateGraph(ReplenishmentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_conditional_edges("tools", after_tools)

    return graph.compile()


def run_replenishment_agent(store_id: int, item_id: int) -> dict:
    """
    Run the replenishment agent for one store-item pair.

    Returns:
        {
            "draft_order_id": int or None,
            "order_needed": bool,
            "messages": list,
            "summary": str,
        }
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Export it before running the agent.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )

    graph = _build_graph()

    initial_state: ReplenishmentState = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=f"Run replenishment check for store_id={store_id}, item_id={item_id}."
            ),
        ],
        "store_id": store_id,
        "item_id": item_id,
        "draft_order_id": None,
    }

    final_state = graph.invoke(initial_state)
    messages = final_state["messages"]

    # Extract draft_order_id from tool messages
    import json
    draft_order_id = None
    order_details = {}
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "draft_order":
            try:
                content = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if isinstance(content, dict) and "order_id" in content:
                    draft_order_id = content["order_id"]
                    order_details = content
            except Exception:
                pass

    # Final agent message as summary
    summary = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            summary = msg.content
            break

    return {
        "draft_order_id": draft_order_id,
        "order_needed": draft_order_id is not None,
        "order_details": order_details,
        "summary": summary,
        "messages": messages,
    }
