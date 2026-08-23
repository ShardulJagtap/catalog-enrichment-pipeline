"""
agents/quality_scoring_agent.py
---------------------------------
Quality Scoring Agent — assigns a completeness and confidence score
(0–100) to each enriched product SKU.

Scoring breakdown (100 points total):
  Required fields present       40 pts   (up to 40 — 6 fields × ~6.7 pts each)
  Optional fields present       20 pts   (up to 20 — 8 fields × 2.5 pts each)
  Description quality           20 pts   (word count, up to min 50 words)
  No unresolved flags           10 pts   (penalty per MISSING/UNRESOLVED flag)
  No duplicate flag             10 pts   (full 10 if not a duplicate)

Products scoring below HUMAN_REVIEW_THRESHOLD are marked needs_human_review=True.
"""

from __future__ import annotations

from typing import List

from agents.base_agent import BaseAgent
from config.settings import HUMAN_REVIEW_THRESHOLD
from models.product import EnrichedProduct
from utils.helpers import word_count

# ── Scoring weights ────────────────────────────────────────────────────────────
REQUIRED_FIELDS = ["sku", "product_name", "category", "price_usd", "description", "supplier_id"]
OPTIONAL_FIELDS = ["color", "size", "material", "dimensions", "weight_kg", "brand", "subcategory", "seo_tags"]

REQUIRED_WEIGHT = 40   # total pts for required fields
OPTIONAL_WEIGHT = 20   # total pts for optional fields
DESCRIPTION_WEIGHT = 20
FLAG_PENALTY_WEIGHT = 10
DUPLICATE_WEIGHT = 10

DESCRIPTION_MIN_WORDS = 50
DESCRIPTION_GOOD_WORDS = 100


class QualityScoringAgent(BaseAgent):
    """
    Assigns quality_score (0–100) and needs_human_review flag to each product.

    Usage:
        agent = QualityScoringAgent()
        scored_products = agent.execute(enriched_products)
    """

    def run(self, products: List[EnrichedProduct]) -> List[EnrichedProduct]:
        for product in products:
            score = self._score(product)
            product.quality_score = score
            product.needs_human_review = score < HUMAN_REVIEW_THRESHOLD
            if product.needs_human_review:
                product.flags.append(
                    f"HUMAN_REVIEW_REQUIRED:score={score} below threshold={HUMAN_REVIEW_THRESHOLD}"
                )
                self._stats["flagged"] += 1
            self._stats["processed"] += 1

        scores = [p.quality_score for p in products]
        if scores:
            avg = sum(scores) / len(scores)
            self.logger.info(
                "Quality scoring complete: %d products | avg=%.1f | min=%d | max=%d | "
                "needs_review=%d",
                len(products), avg, min(scores), max(scores),
                sum(1 for p in products if p.needs_human_review),
            )
        return products

    # ── Scoring logic ─────────────────────────────────────────────────────────

    def _score(self, p: EnrichedProduct) -> int:
        score = 0

        # 1. Required fields (40 pts)
        pts_per_required = REQUIRED_WEIGHT / len(REQUIRED_FIELDS)
        for field in REQUIRED_FIELDS:
            val = getattr(p, field, None)
            if self._has_value(val):
                score += pts_per_required
            else:
                self.logger.debug(
                    "  [SCORE] Missing required field '%s' for '%s'", field, p.product_name
                )

        # 2. Optional fields (20 pts)
        pts_per_optional = OPTIONAL_WEIGHT / len(OPTIONAL_FIELDS)
        for field in OPTIONAL_FIELDS:
            val = getattr(p, field, None)
            if self._has_value(val):
                score += pts_per_optional

        # 3. Description quality (20 pts)
        wc = word_count(p.description or "")
        if wc >= DESCRIPTION_GOOD_WORDS:
            score += DESCRIPTION_WEIGHT
        elif wc >= DESCRIPTION_MIN_WORDS:
            # Partial credit: linear between min and good
            ratio = (wc - DESCRIPTION_MIN_WORDS) / (DESCRIPTION_GOOD_WORDS - DESCRIPTION_MIN_WORDS)
            score += DESCRIPTION_WEIGHT * 0.6 + DESCRIPTION_WEIGHT * 0.4 * ratio
        elif wc > 0:
            # Has something but under 50 words — 25% credit
            score += DESCRIPTION_WEIGHT * 0.25

        # 4. Flag penalty (10 pts)
        bad_flags = [
            f for f in p.flags
            if any(f.startswith(prefix) for prefix in
                   ["MISSING:", "UNRESOLVED_REQUIRED:", "HIGH_MISSING_RATIO:"])
        ]
        flag_penalty = min(len(bad_flags) * 2.5, FLAG_PENALTY_WEIGHT)
        score += FLAG_PENALTY_WEIGHT - flag_penalty

        # 5. Not a duplicate (10 pts)
        if not p.is_duplicate:
            score += DUPLICATE_WEIGHT

        final = max(0, min(100, round(score)))
        self.logger.debug("  [SCORE] '%s' → %d pts", p.product_name, final)
        return final

    @staticmethod
    def _has_value(val) -> bool:
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
        if isinstance(val, list) and len(val) == 0:
            return False
        return True
