"""
agents/reporting_agent.py
--------------------------
Reporting Agent — produces the final enriched catalog (JSON + CSV) and
a human-readable summary report of the entire pipeline run.

Output files (written to DATA_OUTPUT_DIR):
  enriched_catalog.json     — full enriched product list
  enriched_catalog.csv      — tabular version for spreadsheet tools
  pipeline_report.txt       — summary statistics and flag breakdown
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from config.settings import DATA_OUTPUT_DIR, HUMAN_REVIEW_THRESHOLD
from models.product import EnrichedProduct


class ReportingAgent(BaseAgent):
    """
    Writes enriched catalog files and prints/saves a pipeline summary report.

    Usage:
        agent = ReportingAgent()
        report = agent.execute((enriched_products, agent_stats))
    """

    def run(self, payload: Tuple[List[EnrichedProduct], Dict[str, Any]]) -> Dict[str, Any]:
        products, agent_stats = payload

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = DATA_OUTPUT_DIR / f"enriched_catalog_{timestamp}.json"
        csv_path  = DATA_OUTPUT_DIR / f"enriched_catalog_{timestamp}.csv"
        report_path = DATA_OUTPUT_DIR / f"pipeline_report_{timestamp}.txt"

        # Write outputs
        self._write_json(products, json_path)
        self._write_csv(products, csv_path)
        report = self._build_report(products, agent_stats, timestamp)
        self._write_report(report, report_path)

        # Also print to console
        self.logger.info("\n" + report)
        self._stats["processed"] = len(products)

        return {
            "report": report,
            "json_path": str(json_path),
            "csv_path": str(csv_path),
            "report_path": str(report_path),
            "total_skus": len(products),
        }

    # ── Writers ───────────────────────────────────────────────────────────────

    def _write_json(self, products: List[EnrichedProduct], path: Path) -> None:
        data = [p.to_dict() for p in products]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        self.logger.info("Wrote enriched catalog JSON → %s", path)

    def _write_csv(self, products: List[EnrichedProduct], path: Path) -> None:
        if not products:
            return
        fieldnames = list(products[0].to_dict().keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for p in products:
                row = p.to_dict()
                # Flatten lists to pipe-separated strings for CSV
                for k, v in row.items():
                    if isinstance(v, list):
                        row[k] = " | ".join(str(x) for x in v)
                writer.writerow(row)
        self.logger.info("Wrote enriched catalog CSV  → %s", path)

    def _write_report(self, report: str, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        self.logger.info("Wrote pipeline report       → %s", path)

    # ── Report builder ────────────────────────────────────────────────────────

    def _build_report(
        self,
        products: List[EnrichedProduct],
        agent_stats: Dict[str, Any],
        timestamp: str,
    ) -> str:
        total = len(products)
        if total == 0:
            return "No products processed."

        duplicates   = sum(1 for p in products if p.is_duplicate)
        canonical    = total - duplicates
        needs_review = sum(1 for p in products if p.needs_human_review)
        scores       = [p.quality_score for p in products]
        avg_score    = sum(scores) / len(scores)
        fully_enriched = sum(1 for p in products if p.quality_score >= 80)

        # Flag breakdown
        all_flags: List[str] = []
        for p in products:
            all_flags.extend(p.flags)
        missing_flags    = [f for f in all_flags if f.startswith("MISSING:")]
        unresolved_flags = [f for f in all_flags if f.startswith("UNRESOLVED_")]
        auto_fill_flags  = [f for f in all_flags if f.startswith("AUTO_FILLED:")]
        llm_flags        = [f for f in all_flags if "llm" in f.lower() or "generated" in f.lower()]
        lang_flags       = [f for f in all_flags if f.startswith("original_language:")]
        dedup_flags      = [f for f in all_flags if f.startswith("duplicate_of:")]

        # Score distribution
        score_bins = {"0-49": 0, "50-69": 0, "70-89": 0, "90-100": 0}
        for s in scores:
            if s < 50:   score_bins["0-49"] += 1
            elif s < 70: score_bins["50-69"] += 1
            elif s < 90: score_bins["70-89"] += 1
            else:        score_bins["90-100"] += 1

        # Per-supplier breakdown
        supplier_counts: Dict[str, int] = {}
        for p in products:
            supplier_counts[p.supplier_id] = supplier_counts.get(p.supplier_id, 0) + 1

        lines = [
            "=" * 65,
            "  CATALOG ENRICHMENT PIPELINE — SUMMARY REPORT",
            f"  Run timestamp : {timestamp}",
            "=" * 65,
            "",
            "── VOLUME ──────────────────────────────────────────────────",
            f"  Total SKUs ingested         : {total}",
            f"  Canonical (non-duplicate)   : {canonical}",
            f"  Duplicates removed          : {duplicates}",
            f"  Flagged for human review    : {needs_review}  (score < {HUMAN_REVIEW_THRESHOLD})",
            "",
            "── QUALITY SCORES ──────────────────────────────────────────",
            f"  Average score               : {avg_score:.1f} / 100",
            f"  Fully enriched (≥80)        : {fully_enriched} ({fully_enriched/total*100:.0f}%)",
            f"  Score distribution:",
            f"    0–49  (needs review) : {score_bins['0-49']}",
            f"    50–69 (partial)      : {score_bins['50-69']}",
            f"    70–89 (good)         : {score_bins['70-89']}",
            f"    90–100 (excellent)   : {score_bins['90-100']}",
            "",
            "── ENRICHMENT ACTIONS ──────────────────────────────────────",
            f"  Auto-filled fields          : {len(auto_fill_flags)}",
            f"  LLM-generated descriptions  : {len(llm_flags)}",
            f"  Non-English listings found  : {len(lang_flags)}",
            f"  Duplicates flagged          : {len(dedup_flags)}",
            f"  Missing required fields     : {len(missing_flags)}",
            f"  Unresolved required fields  : {len(unresolved_flags)}",
            "",
            "── SUPPLIER BREAKDOWN ───────────────────────────────────────",
        ]
        for supplier, count in sorted(supplier_counts.items()):
            lines.append(f"  {supplier:<30} : {count} SKUs")

        lines += [
            "",
            "── AGENT EXECUTION STATS ────────────────────────────────────",
        ]
        for agent_name, stats in agent_stats.items():
            lines.append(
                f"  {agent_name:<35}: processed={stats.get('processed', 0)}, "
                f"flagged={stats.get('flagged', 0)}, "
                f"errors={stats.get('errors', 0)}, "
                f"duration={stats.get('duration_s', 0):.3f}s"
            )

        lines += ["", "=" * 65]
        return "\n".join(lines)
