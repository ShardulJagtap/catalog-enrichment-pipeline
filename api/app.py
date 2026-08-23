"""
api/app.py
----------
FastAPI application for the Catalog Enrichment Pipeline.

Endpoints:
  POST /api/run            — trigger a full pipeline run (background job)
  GET  /api/status/{job_id} — poll job status and progress
  GET  /api/results/{job_id} — fetch enriched catalog once complete
  GET  /api/results/{job_id}/download/json — download enriched JSON
  GET  /api/results/{job_id}/download/csv  — download enriched CSV
  GET  /api/jobs           — list all recent jobs
  GET  /api/health         — health check (Ollama + pipeline status)
  GET  /                   — serve the HTML dashboard
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Ensure project root is on sys.path ────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import DATA_INPUT_DIR, DATA_OUTPUT_DIR, OLLAMA_BASE_URL, LLM_MODEL
from pipeline.orchestrator import PipelineOrchestrator
from utils.logger import get_logger

logger = get_logger("API")

# ── Job store (in-memory) ─────────────────────────────────────────────────────
# In production this would be Redis or a database.
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Catalog Enrichment Pipeline API",
    description=(
        "Agentic AI pipeline that ingests raw product feeds, normalises attributes, "
        "deduplicates SKUs, fills gaps with Ollama LLM, generates SEO descriptions, "
        "and scores each product for quality."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (dashboard)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    files: Optional[List[str]] = None   # specific file paths; None = auto-discover
    mock_llm: bool = False


class JobStatus(BaseModel):
    job_id: str
    status: str          # queued | running | complete | failed
    started_at: Optional[str]
    completed_at: Optional[str]
    progress: str
    total_skus: Optional[int]
    avg_quality_score: Optional[float]
    error: Optional[str]


# ── Background pipeline runner ─────────────────────────────────────────────────

def _run_pipeline(job_id: str, file_paths: Optional[List[str]], mock_llm: bool):
    """Execute the pipeline in a background thread and update job state."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = datetime.now().isoformat()
        _jobs[job_id]["progress"] = "Starting pipeline..."

    try:
        # Optionally enable mock mode for this run
        if mock_llm:
            os.environ["MOCK_LLM"] = "true"
        else:
            os.environ.pop("MOCK_LLM", None)

        def progress_callback(msg: str):
            with _jobs_lock:
                _jobs[job_id]["progress"] = msg

        orch = PipelineOrchestrator(progress_callback=progress_callback)
        result = orch.run(file_paths)

        if "error" in result:
            raise RuntimeError(result["error"])

        # Read the enriched catalog from the output JSON
        with open(result["json_path"], encoding="utf-8") as f:
            enriched = json.load(f)

        scores = [p.get("quality_score", 0) for p in enriched]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0

        with _jobs_lock:
            _jobs[job_id].update({
                "status": "complete",
                "completed_at": datetime.now().isoformat(),
                "progress": "Pipeline complete",
                "total_skus": result["total_skus"],
                "avg_quality_score": avg_score,
                "json_path": result["json_path"],
                "csv_path": result["csv_path"],
                "report_path": result["report_path"],
                "report_text": _read_file(result["report_path"]),
                "enriched": enriched,
            })
        logger.info("Job %s completed: %d SKUs, avg score %.1f", job_id, result["total_skus"], avg_score)

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
        with _jobs_lock:
            _jobs[job_id].update({
                "status": "failed",
                "completed_at": datetime.now().isoformat(),
                "progress": f"Failed: {exc}",
                "error": str(exc),
            })


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """Serve the HTML dashboard."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>Dashboard not found. Visit <a href='/docs'>/docs</a> for the API.</h2>")


@app.get("/api/health")
async def health():
    """Check Ollama connectivity and pipeline readiness."""
    import urllib.request
    ollama_ok = False
    model_available = False
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode())
        models = [m["name"].split(":")[0] for m in data.get("models", [])]
        ollama_ok = True
        model_available = LLM_MODEL.split(":")[0] in models
    except Exception:
        pass

    input_files = list(DATA_INPUT_DIR.glob("*.*")) if DATA_INPUT_DIR.exists() else []

    return {
        "status": "ok",
        "ollama": {
            "connected": ollama_ok,
            "url": OLLAMA_BASE_URL,
            "model": LLM_MODEL,
            "model_available": model_available,
        },
        "input_files": [f.name for f in input_files if not f.name.startswith(".")],
        "active_jobs": sum(1 for j in _jobs.values() if j["status"] == "running"),
    }


@app.post("/api/run", status_code=202)
async def run_pipeline(request: RunRequest, background_tasks: BackgroundTasks):
    """
    Trigger a full enrichment pipeline run.
    Returns a job_id to poll for status.
    """
    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "started_at": None,
            "completed_at": None,
            "progress": "Queued",
            "total_skus": None,
            "avg_quality_score": None,
            "error": None,
            "json_path": None,
            "csv_path": None,
            "report_path": None,
            "report_text": None,
            "enriched": None,
        }

    background_tasks.add_task(_run_pipeline, job_id, request.files, request.mock_llm)
    logger.info("Job %s queued (mock_llm=%s, files=%s)", job_id, request.mock_llm, request.files)

    return {"job_id": job_id, "status": "queued", "poll_url": f"/api/status/{job_id}"}


@app.get("/api/status/{job_id}", response_model=JobStatus)
async def job_status(job_id: str):
    """Poll the status of a pipeline run."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        progress=job.get("progress", ""),
        total_skus=job.get("total_skus"),
        avg_quality_score=job.get("avg_quality_score"),
        error=job.get("error"),
    )


@app.get("/api/results/{job_id}")
async def job_results(job_id: str, limit: int = 100, offset: int = 0):
    """Return the enriched catalog for a completed job (paginated)."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if job["status"] != "complete":
        raise HTTPException(status_code=409, detail=f"Job status is '{job['status']}', not 'complete'")

    enriched = job.get("enriched", [])
    total = len(enriched)
    page = enriched[offset: offset + limit]

    return {
        "job_id": job_id,
        "total_skus": total,
        "avg_quality_score": job.get("avg_quality_score"),
        "offset": offset,
        "limit": limit,
        "products": page,
        "report": job.get("report_text", ""),
    }


@app.get("/api/results/{job_id}/download/json")
async def download_json(job_id: str):
    """Download the enriched catalog as JSON."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or job["status"] != "complete":
        raise HTTPException(status_code=404, detail="Job not found or not complete")
    return FileResponse(
        job["json_path"],
        media_type="application/json",
        filename=f"enriched_catalog_{job_id}.json",
    )


@app.get("/api/results/{job_id}/download/csv")
async def download_csv(job_id: str):
    """Download the enriched catalog as CSV."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or job["status"] != "complete":
        raise HTTPException(status_code=404, detail="Job not found or not complete")
    return FileResponse(
        job["csv_path"],
        media_type="text/csv",
        filename=f"enriched_catalog_{job_id}.csv",
    )


@app.get("/api/jobs")
async def list_jobs():
    """List all recent pipeline jobs."""
    with _jobs_lock:
        jobs = list(_jobs.values())
    return {
        "total": len(jobs),
        "jobs": [
            {
                "job_id": j["job_id"],
                "status": j["status"],
                "started_at": j.get("started_at"),
                "completed_at": j.get("completed_at"),
                "total_skus": j.get("total_skus"),
                "avg_quality_score": j.get("avg_quality_score"),
            }
            for j in sorted(jobs, key=lambda x: x.get("started_at") or "", reverse=True)
        ],
    }


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a supplier feed file to data/input/."""
    allowed = {".csv", ".json", ".txt", ".xml"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'. Allowed: {allowed}")

    dest = DATA_INPUT_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)
    logger.info("Uploaded file: %s (%d bytes)", file.filename, len(content))
    return {"filename": file.filename, "size_bytes": len(content), "path": str(dest)}


# ── Human-in-the-loop review ──────────────────────────────────────────────────

class ReviewAction(BaseModel):
    action: str          # "approve" | "reject"
    note: Optional[str] = None


@app.get("/api/review/{job_id}")
async def get_review_queue(job_id: str):
    """
    Return all SKUs in a completed job that need human review
    (quality_score < HUMAN_REVIEW_THRESHOLD).
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "complete":
        raise HTTPException(status_code=409, detail="Job not complete yet")

    queue = [
        p for p in (job.get("enriched") or [])
        if p.get("needs_human_review")
    ]
    return {
        "job_id": job_id,
        "total_needs_review": len(queue),
        "products": queue,
    }


@app.post("/api/review/{job_id}/{sku}")
async def review_sku(job_id: str, sku: str, body: ReviewAction):
    """
    Approve or reject a single SKU that was flagged for human review.

    - approve: clears needs_human_review, adds audit flag
    - reject:  marks product as rejected, adds audit flag with optional note
    """
    if body.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or job["status"] != "complete":
        raise HTTPException(status_code=404, detail="Job not found or not complete")

    enriched = job.get("enriched") or []
    product = next((p for p in enriched if p.get("sku") == sku), None)
    if not product:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in job '{job_id}'")

    timestamp = datetime.now().isoformat()
    if body.action == "approve":
        product["needs_human_review"] = False
        product["flags"] = [f for f in product.get("flags", [])
                            if "HUMAN_REVIEW_REQUIRED" not in f]
        product["flags"].append(f"HUMAN_APPROVED:{timestamp}")
        logger.info("SKU %s approved by human reviewer (job=%s)", sku, job_id)
    else:
        product["rejected"] = True
        product["needs_human_review"] = False
        note_str = f" note={body.note}" if body.note else ""
        product["flags"].append(f"HUMAN_REJECTED:{timestamp}{note_str}")
        logger.info("SKU %s rejected by human reviewer (job=%s, note=%s)", sku, job_id, body.note)

    return {"job_id": job_id, "sku": sku, "action": body.action, "timestamp": timestamp}
