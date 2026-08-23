"""
agents/normalization_agent.py
------------------------------
Attribute Normalization Agent — standardizes field names, values,
units, and languages across all raw product records.

Responsibilities:
- Map supplier-specific field names to canonical names (via ATTRIBUTE_ALIASES)
- Normalize color abbreviations to full English names
- Parse and standardize prices (strip currency symbols)
- Convert weight units (g -> kg)
- Detect and translate non-English text to English
- Clean and deduplicate flag lists

Justification:
  Supplier feeds use inconsistent naming ("clr", "colour", "Farbe") and
  units ("300g" vs "0.3kg"). Without a dedicated normalization step every
  downstream agent would need its own defensive parsing, creating scattered
  brittle logic. Centralizing here keeps downstream agents clean.
"""

from __future__ import annotations

import concurrent.futures
import re
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.settings import ATTRIBUTE_ALIASES, COLOR_MAP
from models.product import NormalizedProduct, RawProduct
from utils.helpers import clean_string, extract_price, detect_language
from utils.llm_client import translate_to_english

MAX_WORKERS = 4


class NormalizationAgent(BaseAgent):
    """
    Converts a list of RawProduct objects into NormalizedProduct objects.

    Usage:
        agent = NormalizationAgent()
        normalized = agent.execute(raw_products)
    """

    def run(self, raw_products: List[RawProduct]) -> List[NormalizedProduct]:
        normalized: List[NormalizedProduct] = [None] * len(raw_products)  # preserve order

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._normalize, raw): i
                for i, raw in enumerate(raw_products)
            }
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                try:
                    normalized[i] = future.result()
                    self._stats["processed"] += 1
                except Exception as exc:
                    self.logger.error(
                        "Failed to normalize product (supplier=%s, sku=%s): %s",
                        raw_products[i].supplier_id, raw_products[i].supplier_sku, exc,
                    )
                    self._stats["errors"] += 1

        # Filter out None slots (failed products)
        result = [p for p in normalized if p is not None]
        self.logger.info("Normalized %d / %d products", len(result), len(raw_products))
        return result

    # ── Core normalization logic ───────────────────────────────────────────────

    def _normalize(self, raw: RawProduct) -> NormalizedProduct:
        flags: List[str] = []
        fields = raw.raw_fields.copy()

        # Step 1: Remap field names to canonical aliases
        canonical = self._remap_fields(fields)

        # Step 2: Extract and clean individual fields
        product_name = clean_string(canonical.get("product_name")) or clean_string(fields.get("name"))
        supplier_sku = raw.supplier_sku or clean_string(canonical.get("supplier_sku"))
        category = clean_string(canonical.get("category"))
        color_raw = clean_string(canonical.get("color"))
        material = clean_string(canonical.get("material"))
        size = clean_string(canonical.get("size"))
        dimensions = clean_string(canonical.get("dimensions"))
        description = clean_string(canonical.get("description"))
        brand = clean_string(canonical.get("brand"))

        # Step 3: Normalize color
        color = self._normalize_color(color_raw) if color_raw else None

        # Step 4: Parse price
        price_usd = extract_price(canonical.get("price_usd"))

        # Step 5: Parse weight
        weight_kg = self._parse_weight(canonical.get("weight_kg") or fields.get("weight") or fields.get("gewicht"))

        # Step 6: Language detection and translation
        # Cap text length before detection and translation to avoid
        # sending arbitrarily large supplier strings to the LLM.
        MAX_TRANSLATE_CHARS = 500
        text_for_detection = " ".join(filter(None, [product_name, description]))[:MAX_TRANSLATE_CHARS]
        lang = detect_language(text_for_detection) if text_for_detection else "en"

        if lang and lang != "en":
            self.logger.info(
                "  Non-English content detected (lang=%s) for '%s' — translating",
                lang, product_name
            )
            flags.append(f"original_language:{lang}")
            # Only translate fields that are human-readable text
            if product_name:
                product_name = translate_to_english(product_name[:300], lang)
            if description:
                description = translate_to_english(description[:500], lang)
            # Colour names: translate only if short (avoid sending paragraphs)
            if color and len(color) < 40:
                color = translate_to_english(color, lang)

        # Step 7: Normalize category to title case
        if category:
            category = self._normalize_category(category)

        # Step 8: Collect remaining unmapped fields as extras
        mapped_canonical_keys = set(ATTRIBUTE_ALIASES.values()) | {"product_name", "price_usd", "weight_kg"}
        extra = {k: v for k, v in canonical.items() if k not in mapped_canonical_keys and v is not None}

        return NormalizedProduct(
            supplier_id=raw.supplier_id,
            supplier_sku=supplier_sku,
            product_name=product_name,
            category=category,
            price_usd=price_usd,
            color=color,
            size=size,
            material=material,
            dimensions=dimensions,
            weight_kg=weight_kg,
            brand=brand,
            description=description,
            language_detected=lang,
            extra_attributes=extra,
            flags=flags,
        )

    def _remap_fields(self, raw_fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Translate supplier field names to canonical field names using ATTRIBUTE_ALIASES.
        Unknown fields are kept as-is for downstream use.
        """
        canonical: Dict[str, Any] = {}
        for raw_key, value in raw_fields.items():
            # Normalize the key: lowercase, strip whitespace
            normalized_key = raw_key.strip().lower().replace(" ", "_")
            canonical_key = ATTRIBUTE_ALIASES.get(normalized_key, normalized_key)
            # Keep the first mapping if multiple raw keys map to the same canonical key
            if canonical_key not in canonical or canonical[canonical_key] is None:
                canonical[canonical_key] = value
        return canonical

    def _normalize_color(self, raw_color: str) -> str:
        """Map color abbreviations/aliases to standard English names."""
        lowered = raw_color.strip().lower()
        return COLOR_MAP.get(lowered, lowered)

    def _normalize_category(self, category: str) -> str:
        """Title-case and clean a category string."""
        return category.strip().title()

    def _parse_weight(self, raw: Any) -> Optional[float]:
        """Parse weight values like '300g', '2.5kg', '1.2' into kg (float)."""
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        s = str(raw).strip().lower()
        if s.endswith("kg"):
            try:
                return float(s[:-2].strip())
            except ValueError:
                return None
        if s.endswith("g"):
            try:
                return float(s[:-1].strip()) / 1000.0
            except ValueError:
                return None
        # Try bare number
        try:
            return float(s)
        except ValueError:
            return None
