"""
main.py
-------
Entry point for the Agentic AI Catalog Enrichment Pipeline.

Usage:
    python main.py                          # process all files in data/input/
    python main.py data/input/supplier_a.csv data/input/supplier_b.json
    MOCK_LLM=true python main.py            # run without Ollama
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on the path regardless of where the script is run from
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.orchestrator import PipelineOrchestrator
from utils.logger import get_logger

logger = get_logger("Main")


def main():
    # CLI: accept optional file paths as arguments
    file_paths = sys.argv[1:] if len(sys.argv) > 1 else None

    logger.info("Agentic Catalog Enrichment Pipeline — starting")
    try:
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run(file_paths)

        if "error" in result:
            logger.error("Pipeline failed: %s", result["error"])
            sys.exit(1)

        print("\n✔ Pipeline finished successfully.")
        print(f"  Total SKUs : {result['total_skus']}")
        print(f"  JSON output: {result['json_path']}")
        print(f"  CSV output : {result['csv_path']}")
        print(f"  Report     : {result['report_path']}")

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
        sys.exit(0)
    except Exception as exc:
        logger.exception("Unhandled pipeline error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
