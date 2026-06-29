"""
Run the StockFlow API server.

Usage:
    python -m api.run
    python -m api.run --port 8001
    python -m api.run --reload  # dev mode with auto-reload
"""

import argparse
import os
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Run StockFlow API")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")), help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    args = parser.parse_args()

    print(f"Starting StockFlow API on {args.host}:{args.port}")
    if args.reload:
        print("  Auto-reload enabled (dev mode)")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
