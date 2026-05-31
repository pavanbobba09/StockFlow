"""
Interactive replenishment agent demo.

Usage:
    python -m agents.demo                         # picks store 1, item 1
    python -m agents.demo --store-id 3 --item-id 5
    python -m agents.demo --store-id 3 --item-id 5 --auto-approve

Requires: ANTHROPIC_API_KEY env var
"""

import argparse
import os
import sys

from sqlalchemy import text

from data.db import SessionLocal
from agents.tools import _place_order
from agents.replenishment import run_replenishment_agent


def _store_name(session, store_id: int) -> str:
    row = session.execute(
        text("SELECT name FROM stores WHERE id = :sid"), {"sid": store_id}
    ).fetchone()
    return row.name if row else f"Store {store_id}"


def _item_name(session, item_id: int) -> str:
    row = session.execute(
        text("SELECT name, shelf_life_days FROM items WHERE id = :iid"), {"iid": item_id}
    ).fetchone()
    return f"{row.name} (shelf={row.shelf_life_days}d)" if row else f"Item {item_id}"


def print_separator(char="=", width=60):
    print(char * width)


def main():
    parser = argparse.ArgumentParser(description="StockFlow replenishment agent demo")
    parser.add_argument("--store-id",    type=int, default=1)
    parser.add_argument("--item-id",     type=int, default=1)
    parser.add_argument("--auto-approve", action="store_true",
                        help="Automatically approve without prompting (for CI/testing)")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    session = SessionLocal()
    store_name = _store_name(session, args.store_id)
    item_name  = _item_name(session, args.item_id)
    session.close()

    print_separator()
    print("  StockFlow — Replenishment Agent Demo")
    print_separator()
    print(f"  Store : {store_name} (id={args.store_id})")
    print(f"  Item  : {item_name} (id={args.item_id})")
    print_separator()
    print()
    print("Running agent...")
    print()

    result = run_replenishment_agent(args.store_id, args.item_id)

    # Show agent's reasoning (tool calls + final summary)
    from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
    for msg in result["messages"]:
        if isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    args_str = ", ".join(f"{k}={v!r}" for k, v in tc["args"].items())
                    print(f"  → {tc['name']}({args_str})")
            elif msg.content:
                print(f"\n  Agent: {msg.content}")
        elif isinstance(msg, ToolMessage):
            import json
            try:
                content = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                # Show key fields only
                if isinstance(content, dict):
                    display = {k: v for k, v in content.items()
                               if k not in ("idempotency_key",)}
                    print(f"    ↳ {display}")
                else:
                    print(f"    ↳ {content}")
            except Exception:
                print(f"    ↳ {msg.content}")

    print()
    print_separator("-")

    if not result["order_needed"]:
        print("  DECISION: No order needed.")
        if result["summary"]:
            print(f"  Reason: {result['summary']}")
        print_separator()
        return

    # Show proposal
    od = result["order_details"]
    print("  PROPOSAL")
    print_separator("-")
    print(f"  Order ID  : #{result['draft_order_id']}")
    print(f"  Store     : {store_name}")
    print(f"  Item      : {item_name}")
    print(f"  Quantity  : {od.get('quantity', '?')} units")
    if od.get("reason"):
        print(f"  Reason    : {od['reason']}")
    print_separator("-")

    # Human approval
    if args.auto_approve:
        approved = True
        print("  [auto-approve enabled]")
    else:
        answer = input("\n  Approve this order? [y/N]: ").strip().lower()
        approved = answer in ("y", "yes")

    print()
    if approved:
        placed = _place_order(
            order_id=result["draft_order_id"],
            idempotency_key=od.get("idempotency_key", ""),
        )
        print(f"  ✓ Order #{placed['order_id']} placed — status: {placed['status']}")
    else:
        print("  ✗ Order rejected. Draft remains in 'pending' state.")

    print_separator()


if __name__ == "__main__":
    main()
