# Agentic AI Pipeline for Product Catalog Enrichment

An end-to-end multi-agent pipeline that ingests raw product feeds from multiple suppliers, normalises and deduplicates them, maps them to a master schema, fills gaps with a local LLM, generates SEO-optimised descriptions, and scores each SKU for quality — all locally, with no cloud API dependencies.

---

## Architecture overview

```
Supplier A (CSV) ─┐
Supplier B (JSON) ─┤─► IngestionAgent ─► NormalizationAgent ─► DeduplicationAgent
Supplier C (TXT) ─┘                                                     │
                                                                         ▼
                                               SchemaMappingAgent ◄─────┘
                                                       │
                                                       ▼
                                              GapResolutionAgent  (Ollama LLM)
                                                       │
                                                       ▼
                                         DescriptionGenerationAgent (Ollama LLM)
                                                       │
                                                       ▼
                                            QualityScoringAgent
                                                       │
                                                       ▼
                                              ReportingAgent
                                        (JSON + CSV + report.txt)
```

---

## Requirements

| Dependency | Purpose |
|---|---|
| Python ≥ 3.11 | Runtime |
| [Ollama](https://ollama.com) | Local LLM server |
| `llama3.2` model | Default LLM (via Ollama) |

No OpenAI or cloud API keys required.

---

## Setup

### 1. Install Ollama

```bash
brew install ollama          # macOS
# or download from https://ollama.com/download
```

### 2. Pull the model

```bash
ollama pull llama3.2         # ~2 GB, one-time download
```

Other supported models: `mistral`, `llama3.1`, `phi3`, `gemma2`

### 3. Clone and set up the project

```bash
git clone <repo-url>
cd catalog-enrichment-pipeline

python3.11 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

---

## Running the pipeline

### Start Ollama (keep this running)

```bash
ollama serve
# or as a background service: brew services start ollama
```

### Run on all supplier files (default)

```bash
source .venv/bin/activate
python main.py
```

Auto-discovers all files in `data/input/` and processes them through all 8 agents.

### Run on specific files

```bash
python main.py data/input/supplier_a.csv data/input/supplier_b.json
```

### Run without Ollama (mock mode)

Uses rule-based stubs instead of LLM — instant, no model needed:

```bash
MOCK_LLM=true python main.py
```

---

## Output

All output files are written to `data/output/` with a timestamp:

| File | Description |
|---|---|
| `enriched_catalog_<ts>.json` | Full enriched catalog — one object per SKU |
| `enriched_catalog_<ts>.csv` | Same data in tabular format |
| `pipeline_report_<ts>.txt` | Human-readable summary with stats and metrics |

Logs are written per-agent to `logs/` and combined in `logs/pipeline_<date>.log`.

---

## Configuration

Edit `.env` to customise behaviour:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `LLM_MODEL` | `llama3.2` | Ollama model to use |
| `MOCK_LLM` | `false` | Use rule-based stubs instead of Ollama |
| `DEDUP_SIMILARITY_THRESHOLD` | `0.85` | Cosine similarity threshold for deduplication |
| `HUMAN_REVIEW_THRESHOLD` | `50` | Quality score below which human review is flagged |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Running tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

Tests run in mock mode (no Ollama required) and cover:
- Ingestion: CSV, JSON, free-text parsing, missing files, empty inputs
- Normalization: field remapping, color/price/weight parsing, null handling
- Deduplication: exact duplicates, different categories, edge cases
- Quality scoring: score bounds, human review flagging, duplicate penalty

---

## Project structure

```
catalog-enrichment-pipeline/
├── main.py                         # Entry point
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py                 # All config in one place
├── agents/
│   ├── base_agent.py               # Abstract base with timing + logging
│   ├── ingestion_agent.py          # CSV / JSON / TXT / XML parsing
│   ├── normalization_agent.py      # Field remapping, unit conversion, translation
│   ├── deduplication_agent.py      # Rule-based + semantic duplicate detection
│   ├── schema_mapping_agent.py     # Maps to master catalog schema
│   ├── gap_resolution_agent.py     # LLM-based missing field auto-fill
│   ├── description_generation_agent.py  # LLM SEO description generation
│   ├── quality_scoring_agent.py    # 0-100 completeness score
│   └── reporting_agent.py          # JSON + CSV + report output
├── pipeline/
│   └── orchestrator.py             # Wires all agents in sequence
├── models/
│   └── product.py                  # Pydantic models: Raw → Normalized → Enriched
├── utils/
│   ├── llm_client.py               # Ollama HTTP client (no openai SDK)
│   ├── logger.py                   # Per-agent colour logging + file logs
│   └── helpers.py                  # Shared utilities
├── data/
│   ├── input/                      # Supplier feed files
│   ├── output/                     # Enriched catalog output
│   └── schema/master_catalog_schema.json
├── tests/                          # Pytest unit tests
└── docs/architecture.md            # Architecture document
```

---

## Sample results (live run with llama3.2)

```
Total SKUs ingested         : 18
Canonical (non-duplicate)   : 18
Average quality score       : 87.6 / 100
Fully enriched (≥80)        : 18 (100%)
Auto-filled fields          : 15
LLM-generated descriptions  : 18
Non-English listings found  : 4  (German, Tagalog)
```

---

## Architecture document

See [docs/architecture.md](docs/architecture.md) for full design rationale, agent interaction flow, LLM choice justification, and scalability design.
