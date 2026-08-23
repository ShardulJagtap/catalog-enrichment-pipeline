"""
models/product.py
-----------------
Pydantic models for product data at each stage of the pipeline.
Using Pydantic V2 for validation and serialization.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class RawProduct(BaseModel):
    """
    Intermediate representation of a product after ingestion —
    before any normalization or schema mapping.
    """
    supplier_id: str
    supplier_sku: Optional[str] = None
    raw_fields: Dict[str, Any] = Field(default_factory=dict)
    source_format: str = "unknown"   # csv | json | txt
    source_language: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class NormalizedProduct(BaseModel):
    """
    Product after attribute normalization — field names are canonical,
    values are cleaned, language is translated to English.
    """
    supplier_id: str
    supplier_sku: Optional[str] = None
    product_name: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    price_usd: Optional[float] = None
    currency: str = "USD"
    color: Optional[str] = None
    size: Optional[str] = None
    material: Optional[str] = None
    dimensions: Optional[str] = None
    weight_kg: Optional[float] = None
    brand: Optional[str] = None
    description: Optional[str] = None
    language_detected: Optional[str] = None
    extra_attributes: Dict[str, Any] = Field(default_factory=dict)
    flags: List[str] = Field(default_factory=list)

    @field_validator("price_usd", mode="before")
    @classmethod
    def parse_price(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            cleaned = v.strip().lstrip("$€£¥").replace(",", "")
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    @field_validator("weight_kg", mode="before")
    @classmethod
    def parse_weight(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            # Handle "300g" -> 0.3, "2.5kg" -> 2.5
            v = v.strip().lower()
            if v.endswith("kg"):
                try:
                    return float(v[:-2].strip())
                except ValueError:
                    return None
            if v.endswith("g"):
                try:
                    return float(v[:-1].strip()) / 1000.0
                except ValueError:
                    return None
            try:
                return float(v)
            except ValueError:
                return None
        return None


class EnrichedProduct(BaseModel):
    """
    Final enriched product that conforms to the Master Catalog Schema.
    This is the output of the full pipeline.
    """
    sku: str
    product_name: str
    category: str
    subcategory: Optional[str] = None
    price_usd: Optional[float] = None
    currency: str = "USD"
    color: Optional[str] = None
    size: Optional[str] = None
    material: Optional[str] = None
    dimensions: Optional[str] = None
    weight_kg: Optional[float] = None
    brand: Optional[str] = None
    description: str = ""
    seo_tags: Optional[List[str]] = None
    supplier_id: str
    supplier_sku: Optional[str] = None
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None
    language_detected: Optional[str] = None
    quality_score: int = 0
    flags: List[str] = Field(default_factory=list)
    needs_human_review: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
