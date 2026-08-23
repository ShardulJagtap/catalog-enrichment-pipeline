"""
agents/ingestion_agent.py
--------------------------
Ingestion Agent — accepts raw product feeds in CSV, JSON, and free-text
formats and parses them into a uniform list of RawProduct objects.

Responsibilities:
- Auto-detect file format from extension and content sniffing
- Parse CSV with arbitrary column names
- Parse JSON arrays or single objects
- Parse unstructured free-text product listings
- Assign supplier IDs from file naming convention
- Never crash on malformed input — flag and skip bad records

Justification for being an agent:
  Different suppliers have wildly different formats. Centralising all
  ingestion logic here means every downstream agent sees the same
  intermediate format, decoupling format concerns from enrichment logic.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from models.product import RawProduct
from utils.helpers import clean_string


class IngestionAgent(BaseAgent):
    """
    Parses raw supplier files into RawProduct objects.

    Supported formats: CSV, JSON, TXT (free-text / unstructured)

    Usage:
        agent = IngestionAgent()
        raw_products = agent.execute(["data/input/supplier_a.csv", ...])
    """

    FORMAT_HANDLERS = {
        ".csv": "_parse_csv",
        ".json": "_parse_json",
        ".txt": "_parse_txt",
        ".xml": "_parse_xml",
    }

    def run(self, file_paths: List[str]) -> List[RawProduct]:
        all_products: List[RawProduct] = []

        for file_path in file_paths:
            path = Path(file_path)
            if not path.exists():
                self.logger.warning("File not found, skipping: %s", file_path)
                self._stats["errors"] += 1
                continue

            supplier_id = self._extract_supplier_id(path)
            extension = path.suffix.lower()
            handler_name = self.FORMAT_HANDLERS.get(extension)

            if not handler_name:
                self.logger.warning(
                    "Unsupported format '%s' for file: %s — attempting text parse",
                    extension, file_path
                )
                handler_name = "_parse_txt"

            handler = getattr(self, handler_name)
            self.logger.info(
                "Parsing file: %s | format=%s | supplier=%s",
                path.name, extension, supplier_id
            )

            try:
                products = handler(path, supplier_id)
                self.logger.info(
                    "  → Parsed %d product(s) from %s", len(products), path.name
                )
                all_products.extend(products)
                self._stats["processed"] += len(products)
            except Exception as exc:
                self.logger.error("Failed to parse %s: %s", file_path, exc)
                self._stats["errors"] += 1

        self.logger.info("Total raw products ingested: %d", len(all_products))
        return all_products

    # ── Private: Format Parsers ────────────────────────────────────────────────

    def _parse_csv(self, path: Path, supplier_id: str) -> List[RawProduct]:
        products = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                raw = {k.strip().lower(): clean_string(v) for k, v in row.items()}
                sku = raw.get("sku") or raw.get("id") or raw.get("product_id")
                products.append(
                    RawProduct(
                        supplier_id=supplier_id,
                        supplier_sku=sku,
                        raw_fields=raw,
                        source_format="csv",
                    )
                )
        return products

    def _parse_json(self, path: Path, supplier_id: str) -> List[RawProduct]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = [data]
        elif not isinstance(data, list):
            self.logger.warning("Unexpected JSON structure in %s — wrapping", path.name)
            data = [data]

        products = []
        for item in data:
            if not isinstance(item, dict):
                self.logger.warning("Skipping non-dict JSON item: %s", item)
                self._stats["errors"] += 1
                continue
            raw = {k.strip().lower(): v for k, v in item.items()}
            sku = raw.get("sku") or raw.get("id") or raw.get("product_id")
            products.append(
                RawProduct(
                    supplier_id=supplier_id,
                    supplier_sku=str(sku) if sku else None,
                    raw_fields=raw,
                    source_format="json",
                )
            )
        return products

    def _parse_txt(self, path: Path, supplier_id: str) -> List[RawProduct]:
        """
        Parse unstructured free-text product listings.

        Heuristic: Each product starts with a line beginning with 'Product:'.
        Attributes are detected via common patterns like:
          - "Key: Value"
          - "Key - Value"
          - "Key | Value"
        """
        text = path.read_text(encoding="utf-8")
        # Split on blank lines or 'Product:' markers
        blocks = re.split(r'\n\s*\n', text.strip())

        products = []
        for block in blocks:
            if not block.strip():
                continue
            raw = self._parse_text_block(block.strip())
            if not raw:
                continue
            sku = raw.get("sku") or raw.get("id")
            products.append(
                RawProduct(
                    supplier_id=supplier_id,
                    supplier_sku=sku,
                    raw_fields=raw,
                    source_format="txt",
                )
            )
        return products

    def _parse_text_block(self, block: str) -> Dict[str, Any]:
        """Extract key-value pairs from a freetext product block."""
        raw: Dict[str, Any] = {}
        lines = block.splitlines()

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Pattern: "Product: Name" -> name
            m = re.match(r'^Product:\s*(.+)$', line, re.IGNORECASE)
            if m:
                raw["name"] = m.group(1).strip()
                continue

            # Pipe-separated key-value pairs: "Key - Value | Key2 - Value2"
            if '|' in line:
                pairs = line.split('|')
                for pair in pairs:
                    self._extract_kv(pair.strip(), raw)
                continue

            # Single key-value: "Key: Value" or "Key - Value"
            self._extract_kv(line, raw)

        return raw

    def _extract_kv(self, text: str, target: Dict[str, Any]) -> None:
        """Try to extract a key-value pair from a text snippet."""
        for sep in [':', '-', '=']:
            if sep in text:
                parts = text.split(sep, 1)
                if len(parts) == 2:
                    k = parts[0].strip().lower().replace(' ', '_')
                    v = parts[1].strip()
                    if k and v:
                        target[k] = v
                    return

    def _parse_xml(self, path: Path, supplier_id: str) -> List[RawProduct]:
        """Parse XML product feeds using xmltodict."""
        try:
            import xmltodict
        except ImportError:
            self.logger.error("xmltodict not installed — cannot parse XML: %s", path)
            return []

        with open(path, encoding="utf-8") as f:
            data = xmltodict.parse(f.read())

        # Attempt to find the product list — handle common XML structures
        products_raw = []
        for key in ["products", "items", "catalog", "feed"]:
            if key in data:
                val = data[key]
                inner = list(val.values())[0] if isinstance(val, dict) else val
                if isinstance(inner, list):
                    products_raw = inner
                    break
                elif isinstance(inner, dict):
                    products_raw = [inner]
                    break

        products = []
        for item in products_raw:
            raw = {k.lower(): v for k, v in item.items() if not k.startswith('@')}
            sku = raw.get("sku") or raw.get("id")
            products.append(
                RawProduct(
                    supplier_id=supplier_id,
                    supplier_sku=str(sku) if sku else None,
                    raw_fields=raw,
                    source_format="xml",
                )
            )
        return products

    @staticmethod
    def _extract_supplier_id(path: Path) -> str:
        """
        Derive supplier ID from filename.
        e.g. supplier_a.csv -> SUPPLIER_A, feed_B.json -> FEED_B
        """
        stem = path.stem.upper()
        return stem
