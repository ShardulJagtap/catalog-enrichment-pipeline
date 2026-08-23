"""
tests/test_normalization_agent.py
Tests for the NormalizationAgent — field remapping, color/price/weight parsing.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MOCK_LLM", "true")   # never hit real LLM in tests

from agents.normalization_agent import NormalizationAgent
from models.product import NormalizedProduct, RawProduct


@pytest.fixture
def agent():
    return NormalizationAgent()


def make_raw(fields: dict, supplier_id="SUP_TEST", sku=None) -> RawProduct:
    return RawProduct(supplier_id=supplier_id, supplier_sku=sku, raw_fields=fields, source_format="json")


# ── Field alias remapping ──────────────────────────────────────────────────────

def test_title_remaps_to_product_name(agent):
    raw = make_raw({"title": "Men's Jeans", "cost": "29.99", "clr": "blue"})
    result = agent.execute([raw])
    assert len(result) == 1
    assert result[0].product_name == "Men's Jeans"


def test_cost_remaps_to_price_usd(agent):
    raw = make_raw({"title": "Widget", "cost": "49.95"})
    result = agent.execute([raw])
    assert result[0].price_usd == pytest.approx(49.95)


def test_clr_remaps_to_color(agent):
    raw = make_raw({"name": "Shirt", "clr": "wht"})
    result = agent.execute([raw])
    assert result[0].color == "white"


def test_sz_remaps_to_size(agent):
    raw = make_raw({"title": "Jeans", "sz": "32x30"})
    result = agent.execute([raw])
    assert result[0].size == "32x30"


# ── Color normalization ────────────────────────────────────────────────────────

def test_color_abbreviation_blk(agent):
    raw = make_raw({"name": "Headphone", "color": "blk"})
    result = agent.execute([raw])
    assert result[0].color == "black"


def test_color_abbreviation_gry(agent):
    raw = make_raw({"name": "Hoodie", "colour": "gry"})
    result = agent.execute([raw])
    assert result[0].color == "gray"


def test_german_color_blau(agent):
    raw = make_raw({"name": "Shoes", "clr": "blau"})
    result = agent.execute([raw])
    assert result[0].color == "blue"


# ── Price parsing ──────────────────────────────────────────────────────────────

def test_price_with_dollar_sign(agent):
    raw = make_raw({"name": "Mat", "price": "$24.99"})
    result = agent.execute([raw])
    assert result[0].price_usd == pytest.approx(24.99)


def test_null_price_becomes_none(agent):
    raw = make_raw({"name": "Mat", "cost": None})
    result = agent.execute([raw])
    assert result[0].price_usd is None


def test_missing_price_field(agent):
    raw = make_raw({"name": "Widget"})
    result = agent.execute([raw])
    assert result[0].price_usd is None


# ── Weight parsing ─────────────────────────────────────────────────────────────

def test_weight_grams_to_kg(agent):
    raw = make_raw({"name": "Bottle", "weight": "300g"})
    result = agent.execute([raw])
    assert result[0].weight_kg == pytest.approx(0.3)


def test_weight_kg_passthrough(agent):
    raw = make_raw({"name": "Skillet", "weight": "2.5kg"})
    result = agent.execute([raw])
    assert result[0].weight_kg == pytest.approx(2.5)


# ── Resilience ────────────────────────────────────────────────────────────────

def test_empty_list(agent):
    result = agent.execute([])
    assert result == []


def test_all_null_fields(agent):
    raw = make_raw({"name": None, "price": None, "color": None})
    result = agent.execute([raw])
    assert len(result) == 1
    p = result[0]
    assert p.product_name is None
    assert p.price_usd is None
    assert p.color is None
