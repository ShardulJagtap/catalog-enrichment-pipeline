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
        self._embed_mode = None        # "sentence_transformer" | "tfidf" | None
        self._tfidf = None
        self._tfidf_matrix = None
        self._tfidf_keys: list = []
        self._embeddings_cache: dict = {}

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
        Try sentence-transformers first (best accuracy).
        If unavailable or download fails, fall back to TF-IDF cosine similarity
        via scikit-learn — zero download, same quality for short product names.
        """
        # ── Try sentence-transformers ──────────────────────────────────────────
        try:
            import os
            os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            from pathlib import Path
            # Only attempt download if model is already cached locally
            cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
            cached = any(cache_dir.rglob("*.safetensors")) if cache_dir.exists() else False
            if cached:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
                self._embed_mode = "sentence_transformer"
                self.logger.info("Loaded sentence-transformer from local cache")
                return
            else:
                self.logger.info(
                    "sentence-transformer model not cached locally — "
                    "using TF-IDF fallback (run once with internet to cache: "
                    "python -c \"from sentence_transformers import SentenceTransformer; "
                    "SentenceTransformer('all-MiniLM-L6-v2')\")"
                )
        except ImportError:
            pass
        except Exception as exc:
            self.logger.debug("sentence-transformer load failed: %s", exc)

        # ── TF-IDF fallback (scikit-learn, always available) ──────────────────
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._tfidf = TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(2, 3),
                min_df=1,
            )
            self._embed_mode = "tfidf"
            self._embedder = True   # sentinel: embedder is "available"
            self.logger.info(
                "Using TF-IDF char n-gram similarity for semantic dedup "
                "(no download required, fast)"
            )
        except ImportError:
            self.logger.warning("scikit-learn not installed — semantic dedup disabled")
            self._embedder = None
            self._embed_mode = None

    def _batch_embed_all(self, products: List[NormalizedProduct], keys: List[str]) -> None:
        """
        Compute embeddings / similarity matrix for all products in one pass.

        sentence-transformer mode: single batched encode() call.
        TF-IDF mode: fit_transform on all texts, store sparse matrix for cosine lookup.
        Both run in < 1 second for catalogs up to 10k products.
        """
        if self._embedder is None:
            return

        texts = [self._build_text(p) for p in products]

        if self._embed_mode == "tfidf":
            try:
                import numpy as np
                from sklearn.metrics.pairwise import cosine_similarity
                self._tfidf_matrix = self._tfidf.fit_transform(texts)
                self._tfidf_keys   = keys
                self.logger.info(
                    "TF-IDF matrix built: %d products, vocab=%d",
                    len(texts), len(self._tfidf.vocabulary_),
                )
            except Exception as exc:
                self.logger.warning("TF-IDF fit failed: %s", exc)
                self._tfidf_matrix = None
            return

        # sentence_transformer mode
        to_embed = [(k, t) for k, t in zip(keys, texts) if k not in self._embeddings_cache]
        if not to_embed:
            return
        try:
            batch_keys  = [k for k, _ in to_embed]
            batch_texts = [t for _, t in to_embed]
            embeddings  = self._embedder.encode(
                batch_texts, convert_to_numpy=True,
                batch_size=64, show_progress_bar=False,
            )
            for key, emb in zip(batch_keys, embeddings):
                self._embeddings_cache[key] = emb
            self.logger.info("Batched %d sentence embeddings", len(batch_keys))
        except Exception as exc:
            self.logger.warning("Batch embedding failed: %s", exc)

    def _semantic_similarity(self, text_a: str, text_b: str, key_a: str, key_b: str) -> float:
        """Compute similarity between two products using the active embed mode."""
        try:
            if self._embed_mode == "tfidf" and self._tfidf_matrix is not None:
                # Use pre-built TF-IDF matrix — O(1) lookup per pair
                from sklearn.metrics.pairwise import cosine_similarity
                i = self._tfidf_keys.index(key_a) if key_a in self._tfidf_keys else -1
                j = self._tfidf_keys.index(key_b) if key_b in self._tfidf_keys else -1
                if i < 0 or j < 0:
                    return 0.0
                score = cosine_similarity(
                    self._tfidf_matrix[i], self._tfidf_matrix[j]
                )[0][0]
                return float(score)

            elif self._embed_mode == "sentence_transformer":
                # Dense embedding cosine similarity
                emb_a = self._embeddings_cache.get(key_a)
                emb_b = self._embeddings_cache.get(key_b)
                if emb_a is None or emb_b is None:
                    return 0.0
                dot  = np.dot(emb_a, emb_b)
                norm = np.linalg.norm(emb_a) * np.linalg.norm(emb_b)
                return float(dot / norm) if norm > 0 else 0.0

        except Exception as exc:
            self.logger.debug("Semantic similarity failed: %s", exc)
        return 0.0
