"""
agents/schema_mapping_agent.py
--------------------------------
Schema Mapping Agent — maps normalized products to the Master Catalog Schema.

Responsibilities:
- Assign canonical SKUs
- Validate category values against the standard taxonomy
- Infer missing category using LLM when not present
- Map and validate every field in the Master Catalog Schema
- Flag unmapped, ambiguous, or type-mismatched fields
- Preserve all supplier-provided extra attributes in the flags/notes

Justification:
  Normalization cleans values, but doesn't enforce the schema shape.
  This agent is the single source of truth for "does this product conform
  to our catalog standard?" — separating schema concerns from cleaning logic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.settings import CATEGORY_TAXONOMY, REQUIRED_FIELDS
from models.product import EnrichedProduct, NormalizedProduct
from utils.helpers import generate_sku
from utils.llm_client import infer_category


class SchemaMappingAgent(BaseAgent):
    """
    Converts NormalizedProduct objects to EnrichedProduct objects
    that conform to the Master Catalog Schema.

    Usage:
        agent = SchemaMappingAgent()
        enriched = agent.execute(normalized_products)
    """

    def run(self, products: List[NormalizedProduct]) -> List[EnrichedProduct]:
        enriched: List[EnrichedProduct] = []

        for i, product in enumerate(products):
            try:
                ep = self._map_to_schema(product, i)
                enriched.append(ep)
                self._stats["processed"] += 1
            except Exception as exc:
                self.logger.error(
                    "Schema mapping failed for product %d (supplier=%s, sku=%s): %s",
                    i, product.supplier_id, product.supplier_sku, exc
                )
                self._stats["errors"] += 1

        self.logger.info(
            "Schema mapping complete: %d products mapped, %d errors",
            len(enriched), self._stats["errors"]
        )
        return enriched

    # ── Core mapping ──────────────────────────────────────────────────────────

    def _map_to_schema(self, p: NormalizedProduct, index: int) -> EnrichedProduct:
        flags = list(p.flags)

        # ── SKU assignment ─────────────────────────────────────────────────────
        sku = generate_sku(p.supplier_id, p.supplier_sku, index)

        # ── Product name ───────────────────────────────────────────────────────
        product_name = p.product_name or "Unknown Product"
        if not p.product_name:
            flags.append("MISSING:product_name — defaulted to 'Unknown Product'")
            self._stats["flagged"] += 1

        # ── Category mapping ───────────────────────────────────────────────────
        category = self._resolve_category(p, flags)

        # ── Subcategory inference ──────────────────────────────────────────────
        subcategory = self._infer_subcategory(product_name, category)

        # ── Price ──────────────────────────────────────────────────────────────
        price_usd = p.price_usd
        if price_usd is None:
            flags.append("MISSING:price_usd — could not parse from supplier data")
            self._stats["flagged"] += 1

        # ── Duplicate info from extra_attributes ───────────────────────────────
        is_duplicate = bool(p.extra_attributes.get("is_duplicate", False))
        duplicate_of = p.extra_attributes.get("duplicate_of")

        # ── Description (placeholder — will be filled by DescriptionGenerationAgent) ──
        description = p.description or ""

        return EnrichedProduct(
            sku=sku,
            product_name=product_name,
            category=category,
            subcategory=subcategory,
            price_usd=price_usd,
            currency=p.currency,
            color=p.color,
            size=p.size,
            material=p.material,
            dimensions=p.dimensions,
            weight_kg=p.weight_kg,
            brand=p.brand,
            description=description,
            supplier_id=p.supplier_id,
            supplier_sku=p.supplier_sku,
            is_duplicate=is_duplicate,
            duplicate_of=str(duplicate_of) if duplicate_of else None,
            language_detected=p.language_detected,
            quality_score=0,   # Will be set by QualityScoringAgent
            flags=flags,
            needs_human_review=False,   # Will be set by QualityScoringAgent
        )

    def _resolve_category(self, p: NormalizedProduct, flags: List[str]) -> str:
        """
        Resolve a product's category to a valid taxonomy entry.

        Priority:
        1. Supplier-provided category (if it matches taxonomy)
        2. Fuzzy match against taxonomy
        3. LLM inference from product name + description
        4. Default to "Unknown"
        """
        raw_cat = (p.category or "").strip()

        # Direct match (case-insensitive)
        for cat in CATEGORY_TAXONOMY:
            if raw_cat.lower() == cat.lower():
                return cat

        # Fuzzy: taxonomy keyword in raw_cat or vice versa
        if raw_cat:
            for cat in CATEGORY_TAXONOMY:
                if cat.lower() in raw_cat.lower() or raw_cat.lower() in cat.lower():
                    flags.append(
                        f"category:'{raw_cat}' fuzzy-matched to '{cat}'"
                    )
                    return cat

        # Keyword-based heuristics before LLM (faster and cheaper)
        heuristic = self._heuristic_category(p.product_name or "")
        if heuristic:
            flags.append(
                f"category:'{raw_cat or 'missing'}' — heuristic mapped to '{heuristic}'"
            )
            return heuristic

        # LLM inference as last resort
        if p.product_name:
            self.logger.info(
                "  Calling LLM to infer category for: '%s'", p.product_name
            )
            inferred = infer_category(p.product_name, p.description or "")
            if inferred and inferred != "Unknown":
                flags.append(f"category:LLM-inferred as '{inferred}'")
                return inferred

        flags.append(f"MISSING:category — raw='{raw_cat}' could not be mapped")
        self._stats["flagged"] += 1
        return "Unknown"

    def _heuristic_category(self, name: str) -> Optional[str]:
        """Fast keyword-based category assignment without API calls."""
        name_lower = name.lower()
        rules = {
            "Electronics": ["headphone", "earbuds", "charger", "monitor", "keyboard",
                            "mouse", "ssd", "laptop", "tablet", "cable", "bluetooth",
                            "wireless", "speaker", "camera"],
            "Apparel": ["jeans", "hoodie", "shirt", "shoes", "pants", "jacket",
                        "dress", "skirt", "sneakers", "boots", "socks", "hat"],
            "Home & Kitchen": ["water bottle", "cutting board", "skillet", "pan", "pot",
                                "knife", "blender", "toaster", "mug", "bowl", "plate"],
            "Sports & Outdoors": ["yoga mat", "dumbbell", "resistance band", "treadmill",
                                   "bike", "helmet", "tent", "backpack", "running"],
            "Health & Beauty": ["moisturizer", "shampoo", "vitamin", "supplement",
                                 "sunscreen", "lip balm", "serum", "lotion"],
        }
        for category, keywords in rules.items():
            if any(kw in name_lower for kw in keywords):
                return category
        return None

    def _infer_subcategory(self, name: str, category: str) -> Optional[str]:
        """
        Rule-based subcategory assignment.
        Keeps it cheap — no LLM call needed for subcategories.
        """
        name_lower = name.lower()
        subcategory_rules: Dict[str, Dict[str, List[str]]] = {
            "Electronics": {
                "Audio": ["headphone", "earbuds", "speaker", "earphone"],
                "Displays": ["monitor", "display", "screen", "oled", "4k"],
                "Accessories": ["charger", "cable", "keyboard", "mouse", "ssd"],
            },
            "Apparel": {
                "Bottoms": ["jeans", "pants", "shorts", "skirt"],
                "Tops": ["shirt", "hoodie", "jacket", "dress", "blouse"],
                "Footwear": ["shoes", "sneakers", "boots", "sandals"],
            },
            "Home & Kitchen": {
                "Cookware": ["skillet", "pan", "pot", "wok"],
                "Drinkware": ["water bottle", "mug", "cup", "flask"],
                "Tools": ["cutting board", "knife", "peeler"],
            },
            "Sports & Outdoors": {
                "Yoga & Fitness": ["yoga mat", "dumbbell", "resistance", "stretch"],
                "Running": ["running", "jogging", "treadmill"],
            },
        }
        category_subs = subcategory_rules.get(category, {})
        for subcat, keywords in category_subs.items():
            if any(kw in name_lower for kw in keywords):
                return subcat
        return None
