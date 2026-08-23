"""
tests/test_deduplication_agent.py
Tests for the DeduplicationAgent — rule-based matching.
Semantic (sentence-transformer) path is mocked to avoid model download in CI.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["MOCK_LLM"] = "true"

from agents.deduplication_agent import DeduplicationAgent
from models.product import NormalizedProduct


@pytest.fixture
def agent():
    """Return a DeduplicationAgent with the embedder load stubbed out (rule-based only)."""
    a = DeduplicationAgent()
    # Replace _load_embedder with a no-op so no model download happens in tests
    a._load_embedder = lambda: None
    return a


def make_product(name, category="Electronics", sku=None, supplier="SUP_A"):
    return NormalizedProduct(
        supplier_id=supplier,
        supplier_sku=sku or name[:6].replace(" ", ""),
        product_name=name,
        category=category,
        price_usd=49.99,
    )


# ── Exact / near-exact duplicates ─────────────────────────────────────────────

def test_exact_name_duplicate(agent):
    p1 = make_product("Wireless Bluetooth Headphones", sku="A001")
    p2 = make_product("Wireless Bluetooth Headphones", sku="B001", supplier="SUP_B")
    result = agent.execute([p1, p2])
    dup_flags = [p for p in result if any("duplicate_of" in f for f in p.flags)]
    assert len(dup_flags) == 1


def test_near_duplicate_title(agent):
    p1 = make_product("Men's Slim Fit Jeans", category="Apparel", sku="A991")
    p2 = make_product("Slim Fit Jeans Men Indigo 32x30", category="Apparel", sku="B995")
    result = agent.execute([p1, p2])
    assert isinstance(result, list)
    assert len(result) == 2


# ── Non-duplicates must NOT be flagged ────────────────────────────────────────

def test_different_products_not_duplicate(agent):
    p1 = make_product("Wireless Bluetooth Headphones", sku="A001")
    p2 = make_product("USB-C Fast Charger 65W", sku="A002")
    result = agent.execute([p1, p2])
    dup_flags = [p for p in result if any("duplicate_of" in f for f in p.flags)]
    assert len(dup_flags) == 0


def test_different_categories_not_duplicate(agent):
    p1 = make_product("Blue Hoodie Large", category="Apparel", sku="B001")
    p2 = make_product("Blue Speaker Wireless", category="Electronics", sku="A001")
    result = agent.execute([p1, p2])
    dup_flags = [p for p in result if any("duplicate_of" in f for f in p.flags)]
    assert len(dup_flags) == 0


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_single_product_no_dedup(agent):
    p1 = make_product("Yoga Mat Non-Slip", category="Sports & Outdoors")
    result = agent.execute([p1])
    assert len(result) == 1
    assert result[0].flags == []


def test_empty_list(agent):
    result = agent.execute([])
    assert result == []


def test_none_product_names_handled(agent):
    p1 = NormalizedProduct(supplier_id="S", supplier_sku="X1", product_name=None)
    p2 = NormalizedProduct(supplier_id="S", supplier_sku="X2", product_name=None)
    result = agent.execute([p1, p2])
    assert len(result) == 2
