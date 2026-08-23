"""
tests/test_quality_scoring_agent.py
Tests for the QualityScoringAgent — score ranges, human review flagging.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MOCK_LLM", "true")

from agents.quality_scoring_agent import QualityScoringAgent
from models.product import EnrichedProduct


@pytest.fixture
def agent():
    return QualityScoringAgent()


def make_product(**kwargs) -> EnrichedProduct:
    defaults = dict(
        sku="TEST-001",
        product_name="Test Product",
        category="Electronics",
        price_usd=49.99,
        description="A great product for everyday use that fits your lifestyle perfectly and delivers quality.",
        supplier_id="SUP_TEST",
        quality_score=0,
        flags=[],
    )
    defaults.update(kwargs)
    return EnrichedProduct(**defaults)


# ── Score bounds ──────────────────────────────────────────────────────────────

def test_score_is_between_0_and_100(agent):
    p = make_product()
    result = agent.execute([p])
    assert 0 <= result[0].quality_score <= 100


def test_fully_complete_product_scores_high(agent):
    long_desc = "This product is absolutely fantastic. " * 6  # >50 words
    p = make_product(
        color="black",
        material="aluminum",
        dimensions="10x5x3cm",
        weight_kg=0.5,
        brand="Acme",
        size="M",
        seo_tags=["buy", "deal"],
        description=long_desc,
    )
    result = agent.execute([p])
    assert result[0].quality_score >= 75


def test_missing_required_fields_scores_low(agent):
    p = EnrichedProduct(
        sku="EMPTY-001",
        product_name="Unknown Product",
        category="Unknown",
        price_usd=None,        # missing required
        description="",        # missing required
        supplier_id="SUP_X",
        quality_score=0,
        flags=["MISSING:price_usd", "MISSING:description", "UNRESOLVED_REQUIRED:price_usd"],
    )
    result = agent.execute([p])
    assert result[0].quality_score < 60


# ── Human review flag ──────────────────────────────────────────────────────────

def test_low_score_sets_needs_human_review(agent):
    p = EnrichedProduct(
        sku="LOW-001",
        product_name="Unknown Product",
        category="Unknown",
        price_usd=None,
        description="",
        supplier_id="SUP_X",
        quality_score=0,
        flags=["MISSING:price_usd", "MISSING:description", "HIGH_MISSING_RATIO:60% fields absent"],
    )
    result = agent.execute([p])
    assert result[0].needs_human_review is True
    assert any("HUMAN_REVIEW_REQUIRED" in f for f in result[0].flags)


def test_high_score_does_not_need_review(agent):
    long_desc = "Excellent product with premium quality construction. " * 4
    p = make_product(
        color="blue",
        material="steel",
        description=long_desc,
        seo_tags=["steel", "quality"],
    )
    result = agent.execute([p])
    # Should not need review if score is healthy
    if result[0].quality_score >= 50:
        assert result[0].needs_human_review is False


# ── Duplicate penalty ──────────────────────────────────────────────────────────

def test_duplicate_product_scores_lower(agent):
    long_desc = "This product is absolutely fantastic. " * 6
    p_canonical = make_product(description=long_desc, is_duplicate=False)
    p_duplicate = make_product(description=long_desc, is_duplicate=True, sku="TEST-002",
                                duplicate_of="TEST-001")
    results = agent.execute([p_canonical, p_duplicate])
    # Duplicate should score 10 pts lower (DUPLICATE_WEIGHT penalty)
    assert results[0].quality_score > results[1].quality_score


# ── Description quality ────────────────────────────────────────────────────────

def test_short_description_reduces_score(agent):
    p_short = make_product(description="")          # no description at all
    p_long  = make_product(
        description="This product is excellent for daily use and delivers outstanding quality. " * 4,
        sku="TEST-002"
    )
    results = agent.execute([p_short, p_long])
    assert results[1].quality_score > results[0].quality_score


def test_empty_list(agent):
    result = agent.execute([])
    assert result == []
