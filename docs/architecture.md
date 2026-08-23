# Architecture Document — Agentic AI Catalog Enrichment Pipeline

---

## 1. Why Agentic AI over traditional ETL or rule-based pipelines

A traditional ETL pipeline is built around deterministic transformations: extract a value, apply a rule, load the result. This works well when the input is clean and predictable. Product catalog data from multiple suppliers is neither.

| Problem | Traditional ETL | Agentic AI |
|---|---|---|
| Inconsistent field names across suppliers | Brittle regex / lookup tables | NormalizationAgent with alias maps + LLM fallback |
| Missing mandatory fields (price, category) | Leave blank or default | GapResolutionAgent reasons from context |
| Unpolished descriptions | Template-fill or ignore | DescriptionGenerationAgent writes natural copy |
| Non-English listings | Manual translation scripts | NormalizationAgent detects language, calls LLM |
| Category mismatch | Static keyword rules | SchemaMappingAgent + LLM inference |
| Near-duplicate products | Exact-string match only | DeduplicationAgent: rule-based + semantic embeddings |

The key advantage is that each agent can **reason about ambiguity** rather than crashing or silently producing bad data. Each agent also logs its decisions with reasoning, creating a full audit trail — something a conventional pipeline cannot easily provide.

---

## 2. Agent interaction flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PIPELINE ORCHESTRATOR                           │
│                                                                     │
│  ┌──────────────┐     List[RawProduct]                              │
│  │  Ingestion   │──────────────────────────────────────────────┐    │
│  │    Agent     │  CSV / JSON / TXT / XML → RawProduct objects  │    │
│  └──────────────┘                                              │    │
│                                                                ▼    │
│  ┌──────────────────┐  List[NormalizedProduct]                      │
│  │  Normalization   │──────────────────────────────────────────┐    │
│  │      Agent       │  Remap fields, clean values, translate   │    │
│  └──────────────────┘                                          │    │
│                                                                ▼    │
│  ┌──────────────────┐  List[NormalizedProduct] + dup flags          │
│  │  Deduplication   │──────────────────────────────────────────┐    │
│  │      Agent       │  Rule-based + semantic similarity        │    │
│  └──────────────────┘                                          │    │
│                                                                ▼    │
│  ┌──────────────────┐  List[EnrichedProduct]                        │
│  │  Schema Mapping  │──────────────────────────────────────────┐    │
│  │      Agent       │  Map to master schema, assign SKUs       │    │
│  └──────────────────┘                                          │    │
│                                                                ▼    │
│  ┌──────────────────┐  List[EnrichedProduct] with gaps filled       │
│  │  Gap Resolution  │──────────────────────────────────────────┐    │
│  │      Agent       │  LLM auto-fill for missing required      │    │
│  └──────────────────┘  fields                                  │    │
│                                                                ▼    │
│  ┌──────────────────────────┐  Enriched with descriptions           │
│  │  Description Generation  │──────────────────────────────────┐    │
│  │          Agent           │  LLM generates ≥60-word SEO copy │    │
│  └──────────────────────────┘  + keyword tags                  │    │
│                                                                ▼    │
│  ┌──────────────────┐  Scored products (0-100)                      │
│  │  Quality Scoring │──────────────────────────────────────────┐    │
│  │      Agent       │  Weighted completeness + confidence      │    │
│  └──────────────────┘                                          │    │
│                                                                ▼    │
│  ┌──────────────────┐                                               │
│  │    Reporting     │  JSON + CSV + text report to data/output/     │
│  │      Agent       │                                               │
│  └──────────────────┘                                               │
└─────────────────────────────────────────────────────────────────────┘
```

Data flows strictly left-to-right through the orchestrator. No agent calls another agent directly — they receive input data and return output data, making each one independently testable.

---

## 3. Agent responsibilities and justification

### IngestionAgent
**Responsibility:** Accept raw product feeds in CSV, JSON, TXT, or XML formats and parse them into a uniform `RawProduct` object.

**Justification:** Supplier formats are wildly inconsistent. Centralising all parsing here means every downstream agent works with the same intermediate structure, completely decoupled from format details.

**Design decisions:**
- Format is auto-detected from file extension
- Unknown formats fall back to free-text parsing rather than crashing
- Supplier ID is derived from the filename

---

### NormalizationAgent
**Responsibility:** Standardise field names, values, units, and language.

**Justification:** Without normalisation, every downstream agent would need its own defensive parsing. Centralising alias resolution (e.g. `clr` → `color`, `cost` → `price_usd`) and unit conversion (g → kg, `$24.99` → float) keeps all other agents clean.

**Design decisions:**
- Alias map is in `config/settings.py` — easy to extend without touching agent code
- Language detection via `langdetect`; translation via Ollama
- Gracefully handles null fields — never raises on missing data

---

### DeduplicationAgent
**Responsibility:** Identify duplicate and near-duplicate products.

**Two-stage approach:**

**Stage 1 — Rule-based (fast):**
- Normalise product names (lowercase, strip stop words, remove punctuation)
- Compute SequenceMatcher ratio + Jaccard token overlap
- Combined score: 60% sequence ratio + 40% Jaccard
- O(n²) — acceptable for catalogs up to ~50k SKUs

**Stage 2 — Semantic (accurate):**
- Embed product name + attributes using `sentence-transformers/all-MiniLM-L6-v2`
- Cosine similarity between embeddings
- Only runs if Stage 1 score is below threshold (avoids redundant LLM calls)

**Justification:** Pure string matching misses "Men's Slim Fit Jeans" vs "Slim Fit Jeans Men Indigo 32x30". Pure semantic matching has false positives on short titles. The two-stage approach balances precision and recall.

**Edge cases handled:**
- Same product, different SKUs from different suppliers → Stage 1 or 2 catches it
- Products in different known categories → never flagged as duplicates (category guard)
- Null product names → handled gracefully, no crash

---

### SchemaMappingAgent
**Responsibility:** Map normalized products to the master catalog schema and assign canonical SKUs.

**Justification:** Normalization cleans values but does not enforce schema shape. This agent is the single source of truth for "does this product conform to our standard?" Separating schema enforcement from data cleaning keeps each concern isolated.

**Category resolution priority:**
1. Direct taxonomy match (case-insensitive)
2. Fuzzy substring match
3. Keyword heuristic (fast, no API call)
4. LLM inference (last resort)

---

### GapResolutionAgent
**Responsibility:** Detect missing required fields and attempt auto-fill using Ollama.

**Justification:** Supplier feeds are incomplete by design. A dedicated resolution agent uses all available context (product name, category, existing attributes) to fill gaps intelligently. It knows *when to stop* — if the LLM returns "UNKNOWN" or a low-confidence answer, it flags rather than guesses.

**Design decisions:**
- Deterministic rules first (e.g. `currency` → always "USD") — no LLM call needed
- LLM called only when context exists (product name is available)
- Products missing >50% of all fields are flagged for human review
- Price strings returned by LLM (e.g. `"$25"`) are parsed to float before being set

---

### DescriptionGenerationAgent
**Responsibility:** Generate rich, SEO-optimised product descriptions from structured attributes.

**Prompting strategy:**
- **System prompt:** expert e-commerce copywriter persona
- **User prompt:** structured attribute block (never free text) — prevents hallucination
- **Temperature:** 0.7 — creative but grounded
- **Minimum length:** 60 words enforced by prompt instruction
- **Fallback:** rule-based template if Ollama is unavailable

**Justification:** Product descriptions drive conversion rates and SEO. An LLM generates far more natural copy than templates. Providing only structured attributes (not free text) prevents the model from inventing specifications that were not provided.

---

### QualityScoringAgent
**Responsibility:** Assign a completeness and confidence score (0–100) to each enriched SKU.

**Scoring breakdown:**

| Component | Weight | Logic |
|---|---|---|
| Required fields present | 40 pts | 6 fields × 6.67 pts each |
| Optional fields present | 20 pts | 8 fields × 2.5 pts each |
| Description quality | 20 pts | Full at ≥100 words; partial at 50-99; 25% at 1-49 |
| Flag penalty | 10 pts | −2.5 pts per MISSING / UNRESOLVED flag (max −10) |
| Not a duplicate | 10 pts | Full 10 if `is_duplicate=False` |

Products scoring below `HUMAN_REVIEW_THRESHOLD` (default: 50) are flagged `needs_human_review=True`.

---

### ReportingAgent
**Responsibility:** Write enriched catalog files and produce a human-readable pipeline summary.

**Outputs:**
- `enriched_catalog_<timestamp>.json` — full enriched product list
- `enriched_catalog_<timestamp>.csv` — tabular version
- `pipeline_report_<timestamp>.txt` — summary with volume, quality scores, enrichment actions, agent stats

---

## 4. LLM choice and justification

**Model:** `llama3.2` via Ollama (local, open-source)

**Why Ollama + llama3.2:**

| Criterion | Decision |
|---|---|
| Cost | Zero — runs locally, no per-token billing |
| Privacy | Product data never leaves the machine |
| Latency | ~2-5s per call on Apple Silicon with MLX acceleration |
| Quality | llama3.2 handles translation, classification, and copywriting well at this task size |
| Dependency | No API key, no internet connection required |

**Alternative models supported** (set `LLM_MODEL` in `.env`):

| Model | Use case |
|---|---|
| `mistral` | Stronger reasoning, slightly slower |
| `phi3` | Lighter (~2.3GB), good for low-RAM machines |
| `gemma2:2b` | Fastest, minimal quality tradeoff for simple tasks |
| `llama3.1` | Best overall quality if RAM allows |

**Why not GPT-4 / Claude:**
This pipeline is designed for production use where cost at scale (1M+ SKUs) would be prohibitive with cloud LLMs. Ollama provides a cost ceiling of zero and full data sovereignty.

---

## 5. Handling edge cases

### Ambiguous or conflicting attribute values across suppliers
- The NormalizationAgent takes the **first non-null value** when multiple raw keys map to the same canonical key
- Conflicting values are preserved in `extra_attributes` and flagged for audit
- The SchemaMappingAgent logs every category resolution decision with its reasoning

### Products with more than 50% missing fields
- GapResolutionAgent calculates the missing field ratio
- If ratio > 50%, the flag `HIGH_MISSING_RATIO:XX%` is appended
- QualityScoringAgent will score this product low (missing required fields + flag penalty)
- Score < 50 → `needs_human_review=True` is set automatically

### Non-English product listings
- NormalizationAgent runs `langdetect` on the combined product name + description
- If language is not `en`, Ollama is called to translate both fields to English
- `language_detected` field preserved in the enriched product for auditability
- The original language code is added to `flags` (e.g. `original_language:de`)

### Duplicate detection edge cases
- **Same product, different SKU:** Caught by SequenceMatcher token overlap
- **Different categories:** Category guard prevents false positives (a "Blue Hoodie" and a "Blue Speaker" are not duplicates)
- **Abbreviations / reordered words:** Jaccard token similarity catches these regardless of word order
- **Semantic variants:** Sentence-transformer embeddings handle paraphrases the rule-based stage misses

---

## 6. Scalability design — handling 1M+ SKUs in production

The current pipeline is sequential and synchronous, suitable for batch processing up to ~50k SKUs. Scaling to 1M+ requires architectural changes:

### Horizontal scaling
- Replace the in-process orchestrator with a message queue (Kafka, RabbitMQ, or AWS SQS)
- Each agent becomes a stateless microservice consuming from one queue and publishing to the next
- Agents scale independently — deduplication is CPU-bound, description generation is LLM-bound

### LLM throughput
- Deploy multiple Ollama instances behind a load balancer, or move to a GPU cluster
- Use async HTTP calls (`httpx` async client) to batch LLM requests instead of sequential calls
- Cache LLM results for identical inputs (same product name + attributes → same description)

### Deduplication at scale
- Replace O(n²) pairwise comparison with approximate nearest-neighbour search (FAISS, Annoy)
- Pre-cluster products by category before comparing — reduces comparison space dramatically
- Use a vector database (Milvus, Weaviate, Qdrant) for persistent embedding storage

### Storage
- Output to a data warehouse (BigQuery, Redshift, Snowflake) instead of flat files
- Use columnar formats (Parquet) for efficient querying
- Partition by supplier and date for incremental processing

### Streaming ingestion (bonus feature)
- The `STREAMING_MODE` flag in `.env` enables per-product processing as files arrive
- In a production streaming setup, connect to a file watcher or S3 event trigger
- Each new file triggers the full pipeline, outputs are merged into the master catalog

---

## 7. Trade-offs made

| Decision | Trade-off |
|---|---|
| Sequential agent execution | Simpler to reason about and debug vs. async parallel execution which is faster but harder to audit |
| Rule-based category heuristics before LLM | Saves API calls and latency; tradeoff is that complex categories still fall through to LLM |
| `all-MiniLM-L6-v2` for deduplication | Small, fast model — good precision/recall tradeoff vs. larger models that are slower to load |
| Single Ollama call per field in GapResolution | More reliable individual results vs. batching all fields in one call (which often confuses models) |
| `urllib` instead of `httpx` for Ollama | Zero extra dependency vs. async support — acceptable for sequential pipeline |
| Flat file output (JSON/CSV) | Simple and portable vs. database output which requires infra setup |
