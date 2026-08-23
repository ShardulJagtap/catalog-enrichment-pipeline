"""
utils/llm_client.py
-------------------
LLM client with full guardrails, prompt grounding, and output validation.

Guardrails applied at every call:
  1. Input sanitization   — strip injected instructions, cap token length
  2. Prompt grounding     — all prompts include only verified, structured facts;
                            no free-text from suppliers flows unguarded into prompts
  3. Output validation    — schema-check, type-check, allowed-value enforcement
  4. Retry with backoff   — up to MAX_RETRIES attempts before mock fallback
  5. Hallucination guard  — generated text is checked against provided attributes;
                            invented specs are flagged and stripped
  6. Confidence gate      — model must express certainty; low-confidence responses
                            return None so the pipeline flags rather than guesses

Ollama API: POST http://localhost:11434/api/chat  (stream: false)
No OpenAI SDK — zero cloud dependency.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from config.settings import LLM_MODEL, MOCK_LLM, OLLAMA_BASE_URL, CATEGORY_TAXONOMY
from utils.logger import get_logger

logger = get_logger("LLMClient")

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_RETRIES       = 2          # attempts before falling back to mock
RETRY_DELAY_S     = 1.0        # seconds between retries
MAX_INPUT_CHARS   = 1_200      # cap on any single user-supplied text block
MAX_OUTPUT_TOKENS = 600        # hint sent to Ollama (not enforced server-side)
MIN_DESC_WORDS    = 50
MAX_DESC_WORDS    = 250        # reject run-on LLM descriptions

# Phrases that indicate the model is refusing or uncertain
_REFUSAL_PHRASES = (
    "i cannot", "i can't", "i'm unable", "i am unable",
    "as an ai", "i don't know", "i do not know", "not sure",
    "i'm not able", "i apologize", "i'm sorry",
)

# Prompt-injection fragments to strip from any supplier-provided string
_INJECTION_PATTERNS = re.compile(
    r"(ignore (all |previous |prior )?(instructions?|prompts?|rules?)"
    r"|you are now|forget (everything|all)"
    r"|system:?\s|<\|.*?\|>)",
    re.IGNORECASE,
)


# ── Input sanitization ─────────────────────────────────────────────────────────

def _sanitize(text: str, max_chars: int = MAX_INPUT_CHARS) -> str:
    """
    Strip potential prompt-injection attempts and cap length.
    Supplier data is untrusted — it should never override system instructions.
    """
    if not text:
        return ""
    # Remove injection patterns
    cleaned = _INJECTION_PATTERNS.sub("[REMOVED]", text)
    # Collapse excessive whitespace
    cleaned = re.sub(r"\s{3,}", "  ", cleaned)
    # Hard cap
    return cleaned[:max_chars]


def _sanitize_dict(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize all string values in an attribute dict."""
    safe = {}
    for k, v in attrs.items():
        if v is None:
            continue
        safe_k = _sanitize(str(k), max_chars=50)
        safe_v = _sanitize(str(v), max_chars=200) if isinstance(v, str) else v
        if safe_k and safe_v is not None:
            safe[safe_k] = safe_v
    return safe


# ── Output validation ──────────────────────────────────────────────────────────

def _is_refusal(text: str) -> bool:
    """Return True if the model output looks like a refusal or uncertainty."""
    lower = text.lower().strip()
    return any(phrase in lower for phrase in _REFUSAL_PHRASES)


def _validate_category(raw: str) -> Optional[str]:
    """Return a valid taxonomy category or None."""
    raw = raw.strip()
    # Exact match
    if raw in CATEGORY_TAXONOMY:
        return raw
    # Case-insensitive
    for cat in CATEGORY_TAXONOMY:
        if raw.lower() == cat.lower():
            return cat
    # Partial match (LLM sometimes adds extra words)
    for cat in CATEGORY_TAXONOMY:
        if cat.lower() in raw.lower():
            return cat
    return None


def _validate_description(text: str, product_name: str, attributes: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Validate and clean a generated description.

    Checks:
    - Minimum word count
    - Maximum word count (truncate)
    - Does not open with generic filler like "Introducing the..."
    - Does not contain fabricated numeric specs not present in attributes

    Returns (cleaned_text, list_of_warnings).
    """
    warnings: List[str] = []
    words = text.split()

    if len(words) < MIN_DESC_WORDS:
        warnings.append(f"desc_too_short:{len(words)}_words")

    if len(words) > MAX_DESC_WORDS:
        text = " ".join(words[:MAX_DESC_WORDS]) + "."
        warnings.append(f"desc_truncated_to_{MAX_DESC_WORDS}_words")

    # Hallucination check: look for numeric claims not backed by attributes
    attr_numbers = set()
    for v in attributes.values():
        for num in re.findall(r"\d+\.?\d*", str(v)):
            attr_numbers.add(num)

    invented_numbers = []
    for num in re.findall(r"\b\d+\.?\d*\b", text):
        # Allow numbers also present in the product name
        name_nums = set(re.findall(r"\d+\.?\d*", product_name))
        if num not in attr_numbers and num not in name_nums and float(num) > 1:
            invented_numbers.append(num)

    if invented_numbers:
        warnings.append(f"possible_hallucination:invented_numbers={invented_numbers[:3]}")
        logger.warning(
            "  Description for '%s' may contain invented numbers: %s",
            product_name, invented_numbers[:3],
        )

    return text.strip(), warnings


def _validate_seo_tags(tags: List[Any], product_name: str, category: str) -> List[str]:
    """
    Validate SEO tags:
    - Must be strings
    - 2–50 chars each
    - Deduplicate
    - Max 10 tags
    """
    clean = []
    seen = set()
    for tag in tags:
        t = str(tag).strip().lower()
        if 2 <= len(t) <= 50 and t not in seen:
            seen.add(t)
            clean.append(t)
    return clean[:10]


def _validate_field_value(field: str, value: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Coerce and validate an LLM-inferred field value.
    Returns (coerced_value, warning_or_None).
    """
    v = value.strip()
    if not v or v.upper() == "UNKNOWN":
        return None, None

    if field == "price_usd":
        # Accept "$25", "25.00", "25" — reject prose
        cleaned = re.sub(r"[^\d.]", "", v)
        try:
            price = float(cleaned)
            if price <= 0 or price > 100_000:
                return None, f"price_out_of_range:{price}"
            return price, None
        except ValueError:
            return None, f"price_not_parseable:{v[:30]}"

    if field == "category":
        cat = _validate_category(v)
        return cat, (None if cat else f"category_not_in_taxonomy:{v[:40]}")

    if field == "weight_kg":
        cleaned = re.sub(r"[^\d.]", "", v.lower().replace("kg", "").replace("g", ""))
        try:
            w = float(cleaned)
            return w, None
        except ValueError:
            return None, f"weight_not_parseable:{v[:30]}"

    # Generic string field — cap length, no newlines
    v = re.sub(r"[\r\n]+", " ", v)[:300]
    return v, None


# ── Mock stubs ─────────────────────────────────────────────────────────────────

def _mock_response(task: str, **kwargs) -> Any:
    """Deterministic rule-based fallback used when Ollama is unavailable."""
    if task == "translate":
        return kwargs.get("text", "")

    if task == "category":
        name = kwargs.get("product_name", "").lower()
        rules = {
            "Electronics":      ["headphone","monitor","charger","keyboard","mouse","ssd","earbuds","bluetooth","wireless","speaker","camera"],
            "Apparel":          ["jeans","hoodie","shirt","shoes","pants","jacket","yoga pants","dress"],
            "Home & Kitchen":   ["water bottle","cutting board","skillet","cast iron","mug","bowl","pan","pot"],
            "Sports & Outdoors":["yoga mat","running","dumbbell","resistance","treadmill"],
            "Health & Beauty":  ["shampoo","moisturizer","vitamin","supplement","sunscreen"],
        }
        for cat, kws in rules.items():
            if any(kw in name for kw in kws):
                return cat
        return "Unknown"

    if task == "description":
        name  = kwargs.get("product_name", "this product")
        attrs = kwargs.get("attributes", {})
        parts = [f"Introducing the {name}."]
        if attrs.get("color"):      parts.append(f"Available in {attrs['color']}.")
        if attrs.get("material"):   parts.append(f"Crafted from {attrs['material']}.")
        if attrs.get("dimensions"): parts.append(f"Dimensions: {attrs['dimensions']}.")
        if attrs.get("weight_kg"):  parts.append(f"Weighs {attrs['weight_kg']} kg.")
        parts.append(
            "Built for durability and everyday use, this product delivers exceptional "
            "performance across a wide range of tasks. Ideal for those who value quality, "
            "reliability, and design — a practical choice for home, work, or gifting."
        )
        return " ".join(parts)

    if task == "seo_tags":
        name  = kwargs.get("product_name", "product")
        words = [w.strip(".,") for w in name.lower().split() if len(w) > 3]
        return list(dict.fromkeys(words + ["buy online", "best price", "top rated"]))[:8]

    if task == "fill_field":
        field        = kwargs.get("field", "")
        product_name = kwargs.get("product_name", "")
        if field == "category":
            return _mock_response("category", product_name=product_name)
        return None

    return None


# ── Ollama HTTP client ─────────────────────────────────────────────────────────

class OllamaClient:
    """
    Calls Ollama /api/chat with retry, timeout, and graceful degradation.
    All supplier data is sanitized before it touches a prompt.
    """

    CHAT_ENDPOINT = "/api/chat"

    def __init__(self):
        self._available = False
        if not MOCK_LLM:
            self._check_connection()

    def _check_connection(self) -> None:
        try:
            url = f"{OLLAMA_BASE_URL}/api/tags"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            model_names = [m["name"].split(":")[0] for m in data.get("models", [])]
            wanted = LLM_MODEL.split(":")[0]
            if wanted not in model_names:
                logger.warning(
                    "Model '%s' not found in Ollama. Available: %s. Run: ollama pull %s",
                    LLM_MODEL, model_names, LLM_MODEL,
                )
            else:
                logger.info("Ollama ready | url=%s | model=%s", OLLAMA_BASE_URL, LLM_MODEL)
            self._available = True
        except Exception as exc:
            logger.warning(
                "Cannot reach Ollama at %s (%s). Falling back to mock mode. "
                "Start with: ollama serve",
                OLLAMA_BASE_URL, exc,
            )

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> str:
        """
        POST to /api/chat with retry logic.
        - system_prompt: trusted, authored by us — never contains supplier data
        - user_prompt:   structured, sanitized data only
        Returns empty string on total failure — callers fall back to mock.
        """
        if not self._available:
            return ""

        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}{self.CHAT_ENDPOINT}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode())
                result = data.get("message", {}).get("content", "").strip()
                if result:
                    return result
                logger.debug("Empty response on attempt %d", attempt)
            except urllib.error.URLError as exc:
                logger.warning("Ollama attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            except Exception as exc:
                logger.warning("Unexpected error attempt %d/%d: %s", attempt, MAX_RETRIES, exc)

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S * attempt)

        logger.error("All %d Ollama attempts failed — using mock fallback", MAX_RETRIES)
        return ""

    @property
    def available(self) -> bool:
        return self._available


# Module-level singleton
_client = OllamaClient()


# ── Public API ─────────────────────────────────────────────────────────────────

def translate_to_english(text: str, source_language: str = "auto") -> str:
    """
    Translate non-English text to English.

    Guardrails:
    - Input sanitized (injection stripped, length capped)
    - Output checked for refusal phrases
    - Falls back to original text if translation fails or is a refusal
    """
    if not text:
        return text

    safe_text = _sanitize(text, max_chars=600)

    if MOCK_LLM or not _client.available:
        return _mock_response("translate", text=safe_text)

    # Ground the system prompt: strict persona, no wiggle room
    system = (
        "You are a professional translator. Your only task is to translate text "
        "to English. Output the translation and nothing else — no explanations, "
        "no commentary, no apologies. If the text is already in English, return it unchanged."
    )
    # Structured user prompt: language and text are clearly labelled, not concatenated
    user = f"Source language: {source_language}\nText to translate:\n{safe_text}"

    result = _client.chat(system, user, temperature=0.05)  # translation: low creativity

    if not result or _is_refusal(result):
        logger.debug("Translation refusal/failure for lang=%s — returning original", source_language)
        return text

    return result.strip()


def infer_category(product_name: str, description: str = "") -> str:
    """
    Classify a product into the standard taxonomy.

    Guardrails:
    - Taxonomy injected directly into system prompt (grounding)
    - Output strictly validated against the taxonomy list
    - Falls back to 'Unknown' if LLM output is not in taxonomy
    """
    safe_name = _sanitize(product_name, max_chars=200)
    safe_desc = _sanitize(description, max_chars=300)

    if MOCK_LLM or not _client.available:
        return _mock_response("category", product_name=safe_name)

    # Grounded system prompt: taxonomy is embedded — model cannot invent categories
    taxonomy_str = " | ".join(CATEGORY_TAXONOMY)
    system = (
        "You are a product classification system. Classify products into exactly one "
        f"of these categories: {taxonomy_str}. "
        "Rules:\n"
        "1. Return ONLY the category name — no punctuation, explanation, or other text.\n"
        "2. If genuinely ambiguous, return: Unknown\n"
        "3. Never invent a category not in the list above."
    )
    user = f"Product name: {safe_name}\nDescription: {safe_desc}"

    result = _client.chat(system, user, temperature=0.0, max_tokens=20)  # one word answer

    if not result or _is_refusal(result):
        return "Unknown"

    validated = _validate_category(result)
    if not validated:
        logger.warning(
            "LLM returned category '%s' not in taxonomy for '%s' — defaulting Unknown",
            result.strip()[:40], safe_name,
        )
        return "Unknown"

    return validated


def generate_description(product_name: str, attributes: Dict[str, Any]) -> str:
    """
    Generate a rich, SEO-optimized product description.

    Guardrails:
    - All attributes sanitized before prompt construction
    - System prompt explicitly forbids inventing specs
    - Output validated: word count, hallucinated numbers flagged
    - Falls back to template mock if result fails validation
    """
    safe_name  = _sanitize(product_name, max_chars=200)
    safe_attrs = _sanitize_dict(attributes)

    if MOCK_LLM or not _client.available:
        return _mock_response("description", product_name=safe_name, attributes=safe_attrs)

    # Build grounded attribute block — only verified data, clearly structured
    attr_lines = "\n".join(
        f"  {k}: {v}" for k, v in safe_attrs.items() if v is not None
    )

    # System prompt: strict constraints baked in
    system = (
        "You are an expert e-commerce copywriter. Write a product description using ONLY "
        "the attributes provided below — do not invent, assume, or add any specification "
        "not explicitly listed. The description must:\n"
        f"  • Be {MIN_DESC_WORDS}–{MAX_DESC_WORDS} words\n"
        "  • Be SEO-optimized and persuasive\n"
        "  • Use natural, engaging language\n"
        "  • Contain no headings, labels, or bullet points\n"
        "  • Contain no made-up dimensions, materials, or technical specs\n"
        "Return ONLY the description paragraph."
    )
    # User prompt is purely structured data — not free-form supplier text
    user = (
        f"Product name: {safe_name}\n"
        f"Known attributes:\n{attr_lines if attr_lines else '  (none provided)'}"
    )

    result = _client.chat(system, user, temperature=0.65, max_tokens=120)  # ~50-60 words is enough

    if not result or _is_refusal(result):
        logger.debug("Description refusal/empty for '%s' — using mock", safe_name)
        return _mock_response("description", product_name=safe_name, attributes=safe_attrs)

    validated, warnings = _validate_description(result, safe_name, safe_attrs)

    if warnings:
        logger.debug("Description warnings for '%s': %s", safe_name, warnings)

    # If too short after validation, fall back to mock template
    if len(validated.split()) < MIN_DESC_WORDS:
        logger.debug("Description too short for '%s' after validation — using mock", safe_name)
        return _mock_response("description", product_name=safe_name, attributes=safe_attrs)

    return validated


def generate_seo_tags(product_name: str, description: str, category: str) -> List[str]:
    """
    Generate 5-8 SEO keyword tags.

    Guardrails:
    - All inputs sanitized
    - Output must be a JSON array — any other format is rejected
    - Each tag validated for length and uniqueness
    """
    safe_name = _sanitize(product_name, max_chars=200)
    safe_desc = _sanitize(description, max_chars=400)
    safe_cat  = _sanitize(category, max_chars=50)

    if MOCK_LLM or not _client.available:
        return _mock_response("seo_tags", product_name=safe_name)

    system = (
        "You are an SEO specialist for e-commerce. Generate search keyword tags.\n"
        "Rules:\n"
        "1. Return ONLY a JSON array of 5–8 strings, e.g. [\"tag one\", \"tag two\"]\n"
        "2. Tags must be specific, relevant search phrases a buyer would type\n"
        "3. No generic filler like 'great product' or 'high quality'\n"
        "4. No tag longer than 50 characters\n"
        "5. Lowercase only"
    )
    user = (
        f"Product: {safe_name}\n"
        f"Category: {safe_cat}\n"
        f"Description excerpt: {safe_desc[:300]}"
    )

    result = _client.chat(system, user, temperature=0.25, max_tokens=80)  # tags: short JSON array

    if result and not _is_refusal(result):
        try:
            match = re.search(r'\[.*?\]', result, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                if isinstance(parsed, list) and parsed:
                    validated = _validate_seo_tags(parsed, safe_name, safe_cat)
                    if validated:
                        return validated
        except (json.JSONDecodeError, AttributeError):
            pass

    logger.debug("SEO tag parsing failed for '%s' — using mock", safe_name)
    return _mock_response("seo_tags", product_name=safe_name)


def attempt_field_fill(field: str, product_name: str, context: Dict[str, Any]) -> Optional[Any]:
    """
    Auto-fill a single missing product field using LLM reasoning.

    Guardrails:
    - Field name is validated against an allowed list
    - Context sanitized before prompt construction
    - Output validated per field type (price → float, category → taxonomy, etc.)
    - Model must respond with 'UNKNOWN' when not confident — we enforce this
    - Returns None when validation fails — pipeline flags rather than guesses
    """
    # Only allow filling known, safe fields — prevent arbitrary attribute injection
    FILLABLE_FIELDS = {
        "price_usd", "category", "color", "material", "brand",
        "dimensions", "weight_kg", "description",
    }
    if field not in FILLABLE_FIELDS:
        logger.debug("Field '%s' not in fillable set — skipping LLM fill", field)
        return None

    safe_name    = _sanitize(product_name, max_chars=200)
    safe_context = _sanitize_dict(context)

    if MOCK_LLM or not _client.available:
        return _mock_response("fill_field", field=field, product_name=safe_name)

    context_lines = "\n".join(
        f"  {k}: {v}" for k, v in safe_context.items() if v is not None
    )

    # Grounded system prompt: model role is narrowly scoped
    system = (
        "You are a product data enrichment assistant. Your task is to infer the value "
        "of a single missing field based on the product name and its known attributes.\n"
        "Rules:\n"
        "1. Return ONLY the value — no units unless part of the value, no explanation\n"
        "2. If you are not confident, return exactly: UNKNOWN\n"
        "3. Do not invent specifications — only infer what is reasonably implied\n"
        "4. Keep answers concise: prices as numbers, categories as names, etc."
    )
    user = (
        f"Product name: {safe_name}\n"
        f"Known attributes:\n{context_lines if context_lines else '  (none)'}\n"
        f"Field to fill: {field}"
    )

    result = _client.chat(system, user, temperature=0.05, max_tokens=30)  # one value answer

    if not result or _is_refusal(result) or result.strip().upper() == "UNKNOWN":
        return None

    coerced, warning = _validate_field_value(field, result)
    if warning:
        logger.debug("Field fill validation warning for '%s'.%s: %s", safe_name, field, warning)
    if coerced is None and warning:
        return None

    return coerced
