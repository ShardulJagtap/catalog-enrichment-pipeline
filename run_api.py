"""
run_api.py
----------
Launch the FastAPI server.

Usage:
    source .venv/bin/activate
    python run_api.py            # default: http://localhost:8000
    python run_api.py --port 9000
"""

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = parser.parse_args()

    print(f"\n🚀  Catalog Enrichment Pipeline API")
    print(f"    Dashboard : http://localhost:{args.port}")
    print(f"    API docs  : http://localhost:{args.port}/docs")
    print(f"    ReDoc     : http://localhost:{args.port}/redoc\n")

    uvicorn.run(
        "api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
