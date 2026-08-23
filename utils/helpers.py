"""
utils/helpers.py
----------------
Shared utility functions used across multiple agents.
"""

from __future__ import annotations

import re
import json
import unicodedata
from typing import Any, Dict, List, Optional


def clean_string(value: Any) -> Optional[str]:
    """Normalize whitespace and strip a string. Returns None for empty/null."""
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r'\s+', ' ', s)
    return s if s else None


def slugify(text: str) -> str:
    """Convert text to a URL/ID-safe slug."""
    text = unicodedata.normalize('NFKD', text.lower())
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text.strip('-')


def safe_float(value: Any) -> Optional[float]:
    """Parse a float from various formats. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r'[^\d.]', '', value.strip())
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


def load_json_file(path: str) -> Any:
    """Load and parse a JSON file, returning the parsed object."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def word_count(text: str) -> int:
    """Return the number of words in a string."""
    if not text:
        return 0
    return len(text.split())


def detect_language(text: str) -> str:
    """
    Detect language of a text snippet using langdetect.
    Falls back to 'en' if detection fails or library is unavailable.
    """
    if not text or len(text.strip()) < 10:
        return "en"
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "en"


def normalize_color(raw_color: str, color_map: Dict[str, str]) -> str:
    """Map a raw color abbreviation/alias to a standard color name."""
    if not raw_color:
        return raw_color
    lowered = raw_color.strip().lower()
    return color_map.get(lowered, lowered)


def extract_price(raw_price: Any) -> Optional[float]:
    """Extract a float price from strings like '$49.99', '€29,95', '45.00'."""
    if raw_price is None:
        return None
    if isinstance(raw_price, (int, float)):
        return float(raw_price)
    raw = str(raw_price).strip()
    # Remove currency symbols and thousand separators
    cleaned = re.sub(r'[^\d.]', '', raw.replace(',', '.'))
    # If multiple dots exist (e.g. "1.234.56"), keep only last occurrence
    parts = cleaned.rsplit('.', 1)
    if len(parts) == 2:
        cleaned = parts[0].replace('.', '') + '.' + parts[1]
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def generate_sku(supplier_id: str, supplier_sku: Optional[str], index: int) -> str:
    """
    Generate a canonical SKU for a product.
    Format: <SUPPLIER>-<original_sku> or <SUPPLIER>-AUTO-<index>
    """
    prefix = supplier_id.upper().replace(" ", "_")
    if supplier_sku:
        return f"{prefix}-{supplier_sku}"
    return f"{prefix}-AUTO-{index:04d}"


def merge_flags(*flag_lists: List[str]) -> List[str]:
    """Merge multiple flag lists, removing duplicates while preserving order."""
    seen = set()
    result = []
    for flags in flag_lists:
        for flag in (flags or []):
            if flag not in seen:
                seen.add(flag)
                result.append(flag)
    return result
