"""
config/settings.py
------------------
Central configuration loaded from environment variables / .env file.
All agents import from here so config is never scattered across files.

LLM backend: Ollama (local, no API key required).
Ollama exposes an OpenAI-compatible REST API at OLLAMA_BASE_URL/v1,
so we use the openai SDK pointed at localhost.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present (development convenience)
load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_INPUT_DIR = BASE_DIR / "data" / "input"
DATA_OUTPUT_DIR = BASE_DIR / os.getenv("OUTPUT_DIR", "data/output")
SCHEMA_PATH = BASE_DIR / "data" / "schema" / "master_catalog_schema.json"
LOG_DIR = BASE_DIR / "logs"

# Ensure output and log directories exist
DATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── LLM Settings (Ollama) ──────────────────────────────────────────────────────
# Ollama serves an OpenAI-compatible API locally — no API key required.
# Start Ollama with: ollama serve
# Pull the default model with: ollama pull llama3.2
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL: str = os.getenv("LLM_MODEL", "llama3.2")
MOCK_LLM: bool = os.getenv("MOCK_LLM", "false").lower() == "true"

# ── Pipeline Tuning ────────────────────────────────────────────────────────────
DEDUP_SIMILARITY_THRESHOLD: float = float(os.getenv("DEDUP_SIMILARITY_THRESHOLD", "0.85"))
HUMAN_REVIEW_THRESHOLD: int = int(os.getenv("HUMAN_REVIEW_THRESHOLD", "50"))
STREAMING_MODE: bool = os.getenv("STREAMING_MODE", "false").lower() == "true"

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Master Catalog Required Fields ────────────────────────────────────────────
REQUIRED_FIELDS = ["sku", "product_name", "category", "price_usd", "description", "supplier_id"]

# ── Standard Category Taxonomy ────────────────────────────────────────────────
CATEGORY_TAXONOMY = [
    "Electronics",
    "Apparel",
    "Home & Kitchen",
    "Sports & Outdoors",
    "Health & Beauty",
    "Toys & Games",
    "Books & Media",
    "Automotive",
    "Garden & Outdoor",
    "Office Supplies",
    "Unknown",
]

# ── Color normalization map ────────────────────────────────────────────────────
COLOR_MAP = {
    "blk": "black",
    "wht": "white",
    "gry": "gray",
    "grey": "gray",
    "red": "red",
    "blu": "blue",
    "blau": "blue",       # German
    "grn": "green",
    "ylw": "yellow",
    "pnk": "pink",
    "org": "orange",
    "slv": "silver",
    "gld": "gold",
    "brn": "brown",
    "prpl": "purple",
    "rgb": "multicolor",
    "indigo": "indigo",
    "natural brown": "brown",
    "schwarz": "black",   # German
}

# ── Attribute alias map (supplier field -> canonical field) ────────────────────
ATTRIBUTE_ALIASES = {
    # IDs / SKUs
    "id": "supplier_sku",
    "product_id": "supplier_sku",
    "item_id": "supplier_sku",
    # Names
    "title": "product_name",
    "name": "product_name",
    "item_name": "product_name",
    "product_title": "product_name",
    # Prices
    "cost": "price_usd",
    "price": "price_usd",
    "retail_price": "price_usd",
    "msrp": "price_usd",
    # Colors
    "clr": "color",
    "colour": "color",
    "farbe": "color",     # German
    # Descriptions
    "desc": "description",
    "product_description": "description",
    "details": "description",
    # Sizes
    "sz": "size",
    "size": "size",
    # Category
    "cat": "category",
    "category": "category",
    "dept": "category",
    # Material
    "material": "material",
    "fabric": "material",
    # Weight
    "weight": "weight_kg",
    "wt": "weight_kg",
    "gewicht": "weight_kg",   # German
    # Dimensions
    "dimensions": "dimensions",
    "dim": "dimensions",
    "size_dims": "dimensions",
}
