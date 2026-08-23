"""
pipeline/orchestrator.py
--------------------------
Pipeline Orchestrator — wires all agents together in sequence and
manages the flow of data between them.

Agent execution order:
  1. IngestionAgent        raw files   → List[RawProduct]
  2. NormalizationAgent    raw         → List[NormalizedProduct]
  3. DeduplicationAgent    normalized  → List[NormalizedProduct]  (with dup flags)
  4. SchemaMappingAgent    normalized  → List[EnrichedProduct]
  5. GapResolutionAgent    enriched    → List[EnrichedProduct]    (gaps filled)
  6. DescriptionGenAgent   enriched    → List[EnrichedProduct]    (descriptions)
  7. QualityScoringAgent   enriched    → List[EnrichedProduct]    (scores)
  8. ReportingAgent        enriched    → report dict + output files
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Any, Optional

from agents.ingestion_agent import IngestionAgent
from agents.normalization_agent import NormalizationAgent
from agents.deduplication_agent import DeduplicationAgent
from agents.schema_mapping_agent import SchemaMappingAgent
from agents.gap_resolution_agent import GapResolutionAgent
from agents.description_generation_agent import DescriptionGenerationAgent
from agents.quality_scoring_agent import QualityScoringAgent
from agents.reporting_agent import ReportingAgent
from models.product import EnrichedProduct
from utils.logger import get_logger

logger = get_logger("Orchestrator")


class PipelineOrchestrator:
    """
    Runs the full catalog enrichment pipeline end-to-end.

    Usage:
        orch = PipelineOrchestrator()
        result = orch.run(["data/input/supplier_a.csv", ...])
    """

    def __init__(self, progress_callback=None):
        self._progress = progress_callback or (lambda msg: None)
        self.ingestion    = IngestionAgent()
        self.normalization = NormalizationAgent()
        self.deduplication = DeduplicationAgent()
        self.schema_mapping = SchemaMappingAgent()
        self.gap_resolution = GapResolutionAgent()
        self.description_gen = DescriptionGenerationAgent()
        self.quality_scoring = QualityScoringAgent()
        self.reporting = ReportingAgent()

    def run(self, file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Execute the full pipeline.

        Args:
            file_paths: List of input file paths. If None, auto-discovers
                        all files in DATA_INPUT_DIR.

        Returns:
            Report dict with paths to output files and summary stats.
        """
        from config.settings import DATA_INPUT_DIR

        if file_paths is None:
            file_paths = self._discover_inputs(DATA_INPUT_DIR)

        logger.info("=" * 60)
        logger.info("CATALOG ENRICHMENT PIPELINE STARTING")
        logger.info("Input files: %s", file_paths)
        logger.info("=" * 60)

        # ── Step 1: Ingestion ──────────────────────────────────────────────────
        self._progress("Step 1/8 — Ingesting supplier files...")
        raw_products = self.ingestion.execute(file_paths)
        if not raw_products:
            logger.error("No products ingested — aborting pipeline.")
            return {"error": "No products ingested", "total_skus": 0}

        # ── Step 2: Normalization ──────────────────────────────────────────────
        self._progress(f"Step 2/8 — Normalising {len(raw_products)} products...")
        normalized = self.normalization.execute(raw_products)

        # ── Step 3: Deduplication ──────────────────────────────────────────────
        self._progress(f"Step 3/8 — Deduplicating {len(normalized)} products...")
        deduplicated = self.deduplication.execute(normalized)

        # ── Step 4: Schema Mapping ─────────────────────────────────────────────
        self._progress("Step 4/8 — Mapping to master catalog schema...")
        enriched: List[EnrichedProduct] = self.schema_mapping.execute(deduplicated)

        # ── Step 5: Gap Resolution ─────────────────────────────────────────────
        self._progress("Step 5/8 — Resolving missing fields with LLM...")
        enriched = self.gap_resolution.execute(enriched)

        # ── Step 6: Description Generation ────────────────────────────────────
        self._progress("Step 6/8 — Generating SEO descriptions with LLM...")
        enriched = self.description_gen.execute(enriched)

        # ── Step 7: Quality Scoring ────────────────────────────────────────────
        self._progress("Step 7/8 — Scoring enriched products...")
        enriched = self.quality_scoring.execute(enriched)

        # ── Step 8: Reporting ──────────────────────────────────────────────────
        self._progress("Step 8/8 — Writing output files...")
        all_stats = self._collect_stats()
        result = self.reporting.execute((enriched, all_stats))

        logger.info("Pipeline complete. Output: %s", result.get("json_path"))
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _discover_inputs(self, input_dir: Path) -> List[str]:
        """Auto-discover all supported input files in the input directory."""
        supported = {".csv", ".json", ".txt", ".xml"}
        files = [
            str(p) for p in sorted(input_dir.iterdir())
            if p.suffix.lower() in supported and not p.name.startswith(".")
        ]
        logger.info("Auto-discovered %d input file(s): %s", len(files), files)
        return files

    def _collect_stats(self) -> Dict[str, Any]:
        """Gather execution stats from all agents for the report."""
        stats = {}
        for agent in [
            self.ingestion, self.normalization, self.deduplication,
            self.schema_mapping, self.gap_resolution, self.description_gen,
            self.quality_scoring,
        ]:
            stats.update(agent.get_stats())
        return stats
