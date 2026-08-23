"""
tests/test_ingestion_agent.py
Tests for the IngestionAgent — CSV, JSON, and free-text parsing.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.ingestion_agent import IngestionAgent
from models.product import RawProduct


@pytest.fixture
def agent():
    return IngestionAgent()


# ── CSV ────────────────────────────────────────────────────────────────────────

def test_parse_csv(agent, tmp_path):
    csv_file = tmp_path / "test_supplier.csv"
    csv_file.write_text("sku,name,price,color\nS001,Widget,9.99,red\nS002,Gadget,,blue\n")
    result = agent.execute([str(csv_file)])
    assert len(result) == 2
    assert all(isinstance(p, RawProduct) for p in result)
    assert result[0].supplier_sku == "S001"
    assert result[0].raw_fields["name"] == "Widget"
    assert result[0].supplier_id == "TEST_SUPPLIER"


def test_csv_missing_sku(agent, tmp_path):
    csv_file = tmp_path / "test_supplier.csv"
    csv_file.write_text("name,price\nWidget,9.99\n")
    result = agent.execute([str(csv_file)])
    assert len(result) == 1
    assert result[0].supplier_sku is None   # no sku column


# ── JSON ───────────────────────────────────────────────────────────────────────

def test_parse_json_array(agent, tmp_path):
    data = [{"id": "J001", "title": "Jeans", "cost": "29.99"}, {"id": "J002", "title": "Shirt"}]
    json_file = tmp_path / "test_supplier.json"
    json_file.write_text(json.dumps(data))
    result = agent.execute([str(json_file)])
    assert len(result) == 2
    assert result[0].supplier_sku == "J001"
    assert result[0].source_format == "json"


def test_parse_json_single_object(agent, tmp_path):
    data = {"id": "J003", "title": "Pants", "cost": "39.99"}
    json_file = tmp_path / "test_supplier.json"
    json_file.write_text(json.dumps(data))
    result = agent.execute([str(json_file)])
    assert len(result) == 1


# ── Free text ──────────────────────────────────────────────────────────────────

def test_parse_txt(agent, tmp_path):
    txt_file = tmp_path / "test_supplier.txt"
    txt_file.write_text(
        "Product: Stainless Water Bottle\n"
        "Colour - Silver | Weight - 300g\n"
        "Price not listed.\n"
    )
    result = agent.execute([str(txt_file)])
    assert len(result) == 1
    assert result[0].source_format == "txt"
    raw = result[0].raw_fields
    assert "name" in raw or "product" in raw   # parsed product name


def test_parse_txt_multiple_products(agent, tmp_path):
    txt_file = tmp_path / "test_supplier.txt"
    txt_file.write_text(
        "Product: Water Bottle\nColor: Blue\nPrice: $12.99\n\n"
        "Product: Yoga Mat\nColor: Purple\nPrice: $24.99\n"
    )
    result = agent.execute([str(txt_file)])
    assert len(result) == 2


# ── Error handling ─────────────────────────────────────────────────────────────

def test_missing_file_does_not_crash(agent):
    result = agent.execute(["/nonexistent/path/file.csv"])
    assert result == []
    assert agent._stats["errors"] == 1


def test_empty_file_list(agent):
    result = agent.execute([])
    assert result == []
