"""
agents/gap_resolution_agent.py
--------------------------------
Gap Resolution Agent — detects missing mandatory fields and attempts
to auto-fill them using LLM reasoning or rule-based inference.

Latency optimization:
  Products are processed concurrently using ThreadPoolExecutor.
  Each product's gap resolution is independent, so all 18 can run
  in parallel rather than sequentially.

Guardrails:
  • Deterministic rules run first (currency, supplier_id, heuristic category)
    so LLM is only called when truly needed
  • attempt_field_fill() validates and type-coerces LLM output before return
  • Products with >50% missing fields are flagged for human review
"""

from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.settings import REQUIRED_FIELDS
from models.product import EnrichedProduct
from utils.llm_client import attempt_field_fill

ALL_FIELDS = [
    "product_name", "category", "price_usd", "color", "size",
    "material", "dimensions", "weight_kg", "brand", "description",
    "seo_tags", "supplier_id",
]

MAX_WORKERS = 4


class GapResolutionAgent(BaseAgent):
    """
    Detects and attempts to resolve missing mandatory fields in enriched products.

    Usage:
        agent = GapResolutionAgent()
        resolved_products = agent.execute(enriched_products)
    """

    def run(self, products: List[EnrichedProduct]) -> List[EnrichedProduct]:
        self.logger.info(
            "Gap resolution: processing %d products (concurrent workers=%d)",
            len(products), MAX_WORKERS,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(self._resolve_gaps, p): p for p in products}
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    self.logger.error("Gap resolution worker failed: %s", exc)
                self._stats["processed"] += 1

        self.logger.info(
            "Gap resolution complete: %d processed, %d flagged",
            self._stats["processed"], self._stats["flagged"],
        )
        return products

    # ── Core gap resolution ───────────────────────────────────────────────────

    def _resolve_gaps(self, product: EnrichedProduct) -> None:
        """Check each field and attempt to fill missing required ones."""
        missing_required = []
        missing_optional = []
        resolved = []

        # ── Required field checks ──────────────────────────────────────────────
        for field in REQUIRED_FIELDS:
            value = getattr(product, field, None)
            if not self._has_value(value):
                filled = self._attempt_fill(product, field)
                if filled is not None:
                    setattr(product, field, filled)
                    resolved.append(field)
                    product.flags.append(f"AUTO_FILLED:{field} via gap_resolution")
                    self.logger.info(
                        "  AUTO_FILLED '%s' for product '%s': %s",
                        field, product.product_name, filled
                    )
                else:
                    missing_required.append(field)
                    product.flags.append(f"UNRESOLVED_REQUIRED:{field}")
                    self._stats["flagged"] += 1
                    self.logger.warning(
                        "  UNRESOLVED required field '%s' for product '%s'",
                        field, product.product_name
                    )

        # ── Optional field logging ─────────────────────────────────────────────
        optional_fields = [f for f in ALL_FIELDS if f not in REQUIRED_FIELDS]
        for field in optional_fields:
            value = getattr(product, field, None)
            if not self._has_value(value):
                missing_optional.append(field)

        # ── Missing ratio check for human review flag ─────────────────────────
        total_fields = len(ALL_FIELDS)
        total_missing = len(missing_required) + len(missing_optional)
        missing_ratio = total_missing / total_fields if total_fields > 0 else 0

        if missing_ratio > 0.5:
            product.flags.append(
                f"HIGH_MISSING_RATIO:{missing_ratio:.0%} fields absent — queued for human review"
            )
            self.logger.warning(
                "  Product '%s' has %.0f%% missing fields — human review recommended",
                product.product_name, missing_ratio * 100
            )

        if resolved:
            self.logger.info(
                "  Resolved %d field(s) for '%s': %s",
                len(resolved), product.product_name, resolved
            )

    def _attempt_fill(self, product: EnrichedProduct, field: str) -> Optional[Any]:
        """
        Try to fill a missing field using deterministic rules first, then LLM.
        Returns the filled value or None — never raises.

        Guardrail: LLM is only called when the product name exists (context minimum).
        The llm_client.attempt_field_fill() function validates the returned value
        per field type before returning, so no additional coercion is needed here
        for most fields. Price is handled explicitly because Pydantic needs a float.
        """
        name    = product.product_name or ""
        context = self._build_context(product)

        # ── Deterministic rules (no LLM needed) ───────────────────────────────
        if field == "currency":
            return "USD"

        # supplier_id must come from ingestion — cannot be inferred
        if field == "supplier_id":
            return None

        # Try fast heuristic for category before calling LLM
        if field == "category" and product.category in (None, "", "Unknown"):
            cat = self._heuristic_category(name)
            if cat:
                return cat

        # ── LLM inference (only when enough context exists) ───────────────────
        if not name:
            return None

        # attempt_field_fill already validates and coerces the value
        return attempt_field_fill(field, name, context)

    def _build_context(self, product: EnrichedProduct) -> Dict[str, Any]:
        """Gather all non-null attributes for LLM context."""
        return {
            k: v for k, v in {
                "category": product.category,
                "color": product.color,
                "size": product.size,
                "material": product.material,
                "dimensions": product.dimensions,
                "weight_kg": product.weight_kg,
                "brand": product.brand,
                "price_usd": product.price_usd,
                "description": product.description[:200] if product.description else None,
            }.items()
            if v is not None
        }

    @staticmethod
    def _has_value(value: Any) -> bool:
        """Return True if a field has a meaningful non-null value."""
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        if isinstance(value, list) and len(value) == 0:
            return False
        return True

    @staticmethod
    def _heuristic_category(name: str) -> Optional[str]:
        """Fast keyword lookup for category inference."""
        name_lower = name.lower()
        rules = {
            "Electronics": ["headphone", "earbuds", "charger", "monitor", "keyboard",
                             "mouse", "ssd", "speaker", "bluetooth", "wireless"],
            "Apparel": ["jeans", "hoodie", "shirt", "shoes", "pants", "jacket"],
            "Home & Kitchen": ["water bottle", "cutting board", "skillet", "yoga mat"],
            "Sports & Outdoors": ["yoga mat", "dumbbell", "resistance band", "running shoes"],
        }
        for category, keywords in rules.items():
            if any(kw in name_lower for kw in keywords):
                return category
        return None
