"""
agents/description_generation_agent.py
----------------------------------------
Description Generation Agent — generates SEO-optimized product descriptions.

Latency optimization:
  All LLM calls are issued concurrently using ThreadPoolExecutor.
  Ollama handles concurrent HTTP requests so this cuts wall-clock time
  from O(n × call_latency) to O(call_latency) for the batch.
  e.g. 18 products × 2.5s/call = 45s sequential → ~5s concurrent.

Guardrails:
  • Only schema-mapped, validated attributes flow into prompts (no raw supplier text)
  • Generated descriptions are word-count validated post-generation
  • Hallucination guard runs inside llm_client.generate_description()
  • Fallback to template mock if LLM output fails validation
"""

from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from models.product import EnrichedProduct
from utils.helpers import word_count
from utils.llm_client import generate_description, generate_seo_tags, MIN_DESC_WORDS

# Max concurrent Ollama calls — 6 is safe for llama3.2 on 8GB+ RAM.
# Reduce to 3 on machines with <8GB.
MAX_WORKERS = 6


class DescriptionGenerationAgent(BaseAgent):
    """
    Generates or enriches product descriptions and SEO tags concurrently.

    Usage:
        agent = DescriptionGenerationAgent()
        products = agent.execute(enriched_products)
    """

    def run(self, products: List[EnrichedProduct]) -> List[EnrichedProduct]:
        # Split products into those needing work vs already adequate
        needs_work: List[Tuple[int, EnrichedProduct, str]] = []   # (idx, product, action)
        for i, p in enumerate(products):
            wc = word_count(p.description)
            if wc >= MIN_DESC_WORDS:
                # Already adequate — queue SEO tag generation only if missing
                if not p.seo_tags:
                    needs_work.append((i, p, "TAGS_ONLY"))
            elif wc == 0:
                needs_work.append((i, p, "GENERATED"))
            else:
                needs_work.append((i, p, "ENHANCED"))
                p.flags.append(f"description:original_was_{wc}_words_enhanced_by_llm")

        self.logger.info(
            "Description generation: %d products need LLM work (concurrent workers=%d)",
            len(needs_work), MAX_WORKERS,
        )

        # ── Concurrent execution ───────────────────────────────────────────────
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._process_one, idx, product, action): (idx, action)
                for idx, product, action in needs_work
            }
            generated = enhanced = tags_only = 0
            for future in concurrent.futures.as_completed(futures):
                idx, action = futures[future]
                try:
                    future.result()   # raises if _process_one raised
                    if action == "GENERATED": generated += 1
                    elif action == "ENHANCED": enhanced += 1
                    else: tags_only += 1
                except Exception as exc:
                    self.logger.error(
                        "Description worker failed for product[%d]: %s", idx, exc
                    )
                self._stats["processed"] += 1

        skipped = len(products) - len(needs_work)
        self.logger.info(
            "Description generation complete: %d generated, %d enhanced, "
            "%d tags-only, %d already adequate",
            generated, enhanced, tags_only, skipped,
        )
        self._stats["flagged"] = generated + enhanced
        return products

    # ── Per-product worker (runs inside thread pool) ──────────────────────────

    def _process_one(self, idx: int, product: EnrichedProduct, action: str) -> None:
        """Generate description and/or SEO tags for a single product."""
        if action != "TAGS_ONLY":
            self.logger.info(
                "  [worker] %s description for '%s'", action, product.product_name
            )
            attributes = self._build_grounded_attributes(product)
            new_desc   = generate_description(product.product_name, attributes)
            new_wc     = word_count(new_desc)

            if new_wc < MIN_DESC_WORDS:
                product.flags.append(
                    f"description:llm_output_{new_wc}_words_below_minimum_{MIN_DESC_WORDS}"
                )
                self.logger.warning(
                    "  Description for '%s' is %d words (min %d)",
                    product.product_name, new_wc, MIN_DESC_WORDS,
                )
            else:
                product.flags.append(
                    f"description:{action.lower()}_by_llm_{new_wc}_words"
                )
            product.description = new_desc

        # Always generate SEO tags
        product.seo_tags = self._generate_tags(product)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_grounded_attributes(self, product: EnrichedProduct) -> Dict[str, Any]:
        """
        Only pass schema-validated, type-safe fields to the LLM.
        Raw supplier strings never reach this point — they were sanitized upstream.
        """
        attrs: Dict[str, Any] = {}
        if product.category and product.category != "Unknown":
            attrs["category"] = product.category
        if product.subcategory:
            attrs["subcategory"] = product.subcategory
        if product.color:
            attrs["color"] = product.color
        if product.size:
            attrs["size"] = product.size
        if product.material:
            attrs["material"] = product.material
        if product.dimensions:
            attrs["dimensions"] = product.dimensions
        if product.weight_kg is not None:
            attrs["weight_kg"] = f"{product.weight_kg:.2f} kg"
        if product.brand:
            attrs["brand"] = product.brand
        if product.price_usd is not None:
            attrs["price_usd"] = f"${product.price_usd:.2f}"
        return attrs

    def _generate_tags(self, product: EnrichedProduct) -> List[str]:
        try:
            return generate_seo_tags(
                product.product_name or "",
                product.description or "",
                product.category or "Unknown",
            )
        except Exception as exc:
            self.logger.warning("SEO tag failure for '%s': %s", product.product_name, exc)
            return []
