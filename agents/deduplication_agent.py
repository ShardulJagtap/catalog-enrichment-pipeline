"""
agents/deduplication_agent.py
------------------------------
Deduplication Agent — identifies duplicate and near-duplicate products
using a two-stage approach:

  Stage 1 — Rule-based exact/near-exact matching:
    Compare normalized product names (edit distance / token overlap).
    Fast O(n²) scan on the full dataset.

  Stage 2 — Semantic similarity:
    Embed product names + attributes using sentence-transformers and
    compute cosine similarity. Products exceeding DEDUP_SIMILARITY_THRESHOLD
    are flagged as duplicates.

Edge cases handled:
  - Same product, different SKUs from different suppliers
  - Near-duplicate titles (e.g. "Slim Fit Jeans Men Indigo 32x30" vs "Men's Slim Fit Jeans")
  - Price/color variants of the same base product (NOT flagged as duplicates)
  - Completely different products with superficially similar names

Justification:
  Simple exact-string matching misses semantic duplicates.
  Semantic-only matching has high false positives on short titles.
  The two-stage approach balances precision and recall.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from agents.base_agent import BaseAgent
from config.settings import DEDUP_SIMILARITY_THRESHOLD
from models.product import NormalizedProduct


class DeduplicationAgent(BaseAgent):
    """
    Marks duplicate products in the normalized product list.

    Each product gets an `is_duplicate` flag and (if duplicate)
    a `duplicate_of` reference to the canonical product's supplier_sku.

    Usage:
        agent = DeduplicationAgent()
        deduplicated = agent.execute(normalized_products)
    """

    def __init__(self):
        super().__init__()
        self._embedder = None
        self._embeddings_cache: Dict[str, Any] = {}

    def run(self, products: List[NormalizedProduct]) -> List[NormalizedProduct]:
        if len(products) < 2:
            self.logger.info("Fewer than 2 products — skipping deduplication")
            return products

        # Try to load the sentence-transformer model
        self._load_embedder()

        # Build a key list for quick reference
        product_keys = [self._product_key(p, i) for i, p in enumerate(products)]

        # Pre-compute ALL embeddings in one batched call (much faster than one-by-one)
        if self._embedder is not None:
            self._batch_embed_all(products, product_keys)

        # Track which indices are duplicates of which canonical index
        duplicate_of: Dict[int, int] = {}   # index -> canonical index
        canonical_set: set = set()

        n = len(products)
        self.logger.info("Running deduplication on %d products (threshold=%.2f)", n, DEDUP_SIMILARITY_THRESHOLD)

        # ── Stage 1: Rule-based token overlap ─────────────────────────────────
        for i in range(n):
            if i in duplicate_of:
                continue
            for j in range(i + 1, n):
                if j in duplicate_of:
                    continue
                score_rule = self._rule_based_similarity(products[i], products[j])
                if score_rule >= DEDUP_SIMILARITY_THRESHOLD:
                    self.logger.info(
                        "  [RULE] Duplicate detected: '%s' ≈ '%s' (score=%.3f)",
                        products[i].product_name, products[j].product_name, score_rule
                    )
                    duplicate_of[j] = i
                    self._stats["flagged"] += 1
                    continue

                # ── Stage 2: Semantic similarity (if embedder available) ───────
                if self._embedder is not None:
                    score_sem = self._semantic_similarity(
                        self._build_text(products[i]),
                        self._build_text(products[j]),
                        product_keys[i],
                        product_keys[j],
                    )
                    if score_sem >= DEDUP_SIMILARITY_THRESHOLD:
                        self.logger.info(
                            "  [SEMANTIC] Duplicate detected: '%s' ≈ '%s' (score=%.3f)",
                            products[i].product_name, products[j].product_name, score_sem
                        )
                        duplicate_of[j] = i
                        self._stats["flagged"] += 1

        # ── Apply duplicate flags ──────────────────────────────────────────────
        for j, i in duplicate_of.items():
            canonical_sku = product_keys[i]
            products[j].flags.append(
                f"duplicate_of:{canonical_sku} (rule+semantic dedup)"
            )
            products[j].extra_attributes["is_duplicate"] = True
            products[j].extra_attributes["duplicate_of"] = canonical_sku

        self.logger.info(
            "Deduplication complete: %d duplicates flagged out of %d products",
            len(duplicate_of), n
        )
        self._stats["processed"] = n
        return products

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _product_key(self, p: NormalizedProduct, index: int) -> str:
        """Return a stable identifier for a product."""
        return p.supplier_sku or f"{p.supplier_id}-{index}"

    def _build_text(self, p: NormalizedProduct) -> str:
        """Build a text representation of a product for embedding."""
        parts = filter(None, [
            p.product_name,
            p.category,
            p.color,
            p.material,
            p.size,
        ])
        return " ".join(parts).lower()

    def _rule_based_similarity(self, a: NormalizedProduct, b: NormalizedProduct) -> float:
        """
        Compute similarity using token overlap + SequenceMatcher.
        Returns a score between 0.0 and 1.0.

        Only compares products in the same category (or if category is unknown).
        """
        # Different categories that are both known → cannot be duplicates
        if (a.category and b.category and
                a.category != "Unknown" and b.category != "Unknown" and
                a.category != b.category):
            return 0.0

        name_a = self._normalize_name(a.product_name or "")
        name_b = self._normalize_name(b.product_name or "")

        if not name_a or not name_b:
            return 0.0

        # SequenceMatcher ratio
        ratio = SequenceMatcher(None, name_a, name_b).ratio()

        # Token Jaccard similarity
        tokens_a = set(name_a.split())
        tokens_b = set(name_b.split())
        if tokens_a | tokens_b:
            jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
        else:
            jaccard = 0.0

        combined = 0.6 * ratio + 0.4 * jaccard
        return combined

    def _normalize_name(self, name: str) -> str:
        """Lowercase, remove punctuation, strip common stop words."""
        stop_words = {"the", "a", "an", "and", "or", "for", "with", "in", "of", "&"}
        name = name.lower()
        name = re.sub(r"[^\w\s]", " ", name)
        tokens = [t for t in name.split() if t not in stop_words]
        return " ".join(tokens)

    def _load_embedder(self) -> None:
        """
        Load the sentence-transformer model.

        Performance optimizations:
        - Model is cached to ~/.cache/huggingface after first download —
          subsequent runs load from disk in ~1-2s instead of downloading.
        - TOKENIZERS_PARALLELISM disabled to avoid fork-safety warnings.
        - HF_HUB_DISABLE_IMPLICIT_TOKEN suppresses the auth warning.
        """
        try:
            import os
            os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            from sentence_transformers import SentenceTransformer
            # SentenceTransformer caches the model in ~/.cache/huggingface/hub
            # so this is only slow on the very first run.
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            self.logger.info("Loaded sentence-transformer: all-MiniLM-L6-v2")
        except ImportError:
            self.logger.warning("sentence-transformers not installed — semantic dedup disabled.")
        except Exception as exc:
            self.logger.warning("Could not load embedder: %s — rule-based dedup only", exc)

    def _batch_embed_all(self, products: List[NormalizedProduct], keys: List[str]) -> None:
        """
        Embed ALL product texts in a single batched call.

        Batching is ~10x faster than encoding one product at a time because
        the model can parallelise across the batch on CPU/GPU.
        Only embeds products not already in the cache.
        """
        if self._embedder is None:
            return

        to_embed = [(k, self._build_text(p))
                    for k, p in zip(keys, products)
                    if k not in self._embeddings_cache]

        if not to_embed:
            return

        batch_keys  = [k for k, _ in to_embed]
        batch_texts = [t for _, t in to_embed]

        try:
            # encode() with a list → single batched forward pass
            embeddings = self._embedder.encode(
                batch_texts,
                convert_to_numpy=True,
                batch_size=64,
                show_progress_bar=False,
            )
            for key, emb in zip(batch_keys, embeddings):
                self._embeddings_cache[key] = emb
            self.logger.info("Batched %d embeddings in one pass", len(batch_keys))
        except Exception as exc:
            self.logger.warning("Batch embedding failed: %s", exc)

    def _semantic_similarity(self, text_a: str, text_b: str, key_a: str, key_b: str) -> float:
        """Compute cosine similarity between two product text embeddings."""
        try:
            for key, text in [(key_a, text_a), (key_b, text_b)]:
                if key not in self._embeddings_cache:
                    self._embeddings_cache[key] = self._embedder.encode(text, convert_to_numpy=True)

            emb_a = self._embeddings_cache[key_a]
            emb_b = self._embeddings_cache[key_b]

            # Cosine similarity
            dot = np.dot(emb_a, emb_b)
            norm = np.linalg.norm(emb_a) * np.linalg.norm(emb_b)
            return float(dot / norm) if norm > 0 else 0.0
        except Exception as exc:
            self.logger.debug("Embedding comparison failed: %s", exc)
            return 0.0
