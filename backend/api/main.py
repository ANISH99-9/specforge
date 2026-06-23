"""
SpecForge — FastAPI Server
Endpoints:
  POST /api/run          — Full pipeline (SSE streaming)
  POST /api/run-stage    — Single stage in isolation
  POST /api/execute      — Execution layer only
  GET  /api/eval/results — Latest eval results
  POST /api/eval/run     — Trigger batch eval
  GET  /api/health       — Health check
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from pipeline.llm_client import get_llm_client
from pipeline.intent import run_intent_extraction
from pipeline.design import run_system_design
from pipeline.schema import run_schema_generation
from pipeline.refine import run_refinement
from repair.repair_engine import RepairEngine
from execution.executor import run_execution
from validation.validator import validate_app_config, build_dependency_graph
from validation.schema_defs import AppConfig, PipelineRunResult, StageMetrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── App lifecycle ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    llm = get_llm_client()
    available = llm.available_providers()
    logger.info("SpecForge API starting…")
    logger.info(
        "LLM providers with keys: %s | tier: %s → %s → %s",
        ", ".join(available) or "none",
        f"{settings.primary_provider}/{settings.primary_model}",
        f"{settings.fallback_provider}/{settings.fallback_model}",
        f"{settings.emergency_provider}/{settings.emergency_model}",
    )
    if not available:
        logger.warning(
            "No API keys found — pipeline calls will fail. "
            "Set GOOGLE_AI_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY in backend/.env"
        )
    yield
    logger.info("SpecForge API shutting down")


app = FastAPI(
    title="SpecForge API",
    description="Natural Language → App Spec Compiler",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ──────────────────────────────────────

class RunRequest(BaseModel):
    prompt: str
    run_execution_layer: bool = True


class StageRequest(BaseModel):
    stage: str
    input_data: dict


class ExecuteRequest(BaseModel):
    app_config: dict


# ── SSE helpers ────────────────────────────────────────────────────

def _sse(event_type: str, data: dict) -> str:
    payload = json.dumps({"type": event_type, **data}, default=str)
    return f"data: {payload}\n\n"


# ── Full pipeline (SSE) ────────────────────────────────────────────

async def _run_pipeline_stream(request: RunRequest):
    """Generator: runs all 4 stages, yielding SSE events after each."""
    run_id = str(uuid.uuid4())[:8]
    llm    = get_llm_client()
    repair = RepairEngine(llm, max_retries=settings.max_repair_retries)
    all_metrics: list[StageMetrics] = []
    total_cost = 0.0
    t_start = time.monotonic()

    yield _sse("run_start", {"run_id": run_id, "prompt": request.prompt[:200]})

    try:
        # ── Stage 1 ──────────────────────────────────────────────
        yield _sse("stage_start", {"stage": "intent", "label": "Intent Extraction"})
        intent, m1 = await run_intent_extraction(request.prompt, llm, repair)
        all_metrics.append(m1)
        total_cost += m1.cost_usd

        yield _sse("stage_complete", {
            "stage": "intent",
            "duration_ms": m1.duration_ms,
            "cost_usd": m1.cost_usd,
            "repair_iterations": m1.repair_iterations,
            "result": intent.model_dump(),
        })

        # Confidence branching (PRD §6)
        if intent.confidence < 0.4 and len(intent.ambiguities) > 0:
            clarification = intent.ambiguities[0]
            yield _sse("clarification_needed", {
                "message": f"Low confidence ({intent.confidence:.0%}). "
                           f"Please clarify: {clarification}",
                "ambiguities": intent.ambiguities,
                "confidence": intent.confidence,
            })

        # ── Stage 2 ──────────────────────────────────────────────
        yield _sse("stage_start", {"stage": "design", "label": "System Design"})
        arch, m2 = await run_system_design(intent, llm, repair)
        all_metrics.append(m2)
        total_cost += m2.cost_usd

        yield _sse("stage_complete", {
            "stage": "design",
            "duration_ms": m2.duration_ms,
            "cost_usd": m2.cost_usd,
            "repair_iterations": m2.repair_iterations,
            "result": arch.model_dump(),
        })

        # ── Stage 3 ──────────────────────────────────────────────
        yield _sse("stage_start", {"stage": "schema", "label": "Schema Generation (×4 parallel)"})
        ui, api, db, auth, schema_metrics = await run_schema_generation(intent, arch, llm, repair)
        for m in schema_metrics:
            all_metrics.append(m)
            total_cost += m.cost_usd

        yield _sse("stage_complete", {
            "stage": "schema",
            "duration_ms": max(m.duration_ms for m in schema_metrics),
            "cost_usd": sum(m.cost_usd for m in schema_metrics),
            "repair_iterations": sum(m.repair_iterations for m in schema_metrics),
            "result": {
                "ui_schema":   ui.model_dump(),
                "api_schema":  api.model_dump(),
                "db_schema":   db.model_dump(),
                "auth_schema": auth.model_dump(),
            },
        })

        # ── Stage 4 ──────────────────────────────────────────────
        yield _sse("stage_start", {"stage": "refinement", "label": "Consistency Refinement"})
        app_config, m4 = await run_refinement(intent, arch, ui, api, db, auth, run_id, llm, repair)
        all_metrics.append(m4)
        total_cost += m4.cost_usd

        yield _sse("stage_complete", {
            "stage": "refinement",
            "duration_ms": m4.duration_ms,
            "cost_usd": m4.cost_usd,
            "repair_iterations": m4.repair_iterations,
            "result": {
                "assumptions": [a.model_dump() for a in app_config.assumptions],
                "conflicts":   [c.model_dump() for c in app_config.conflicts],
            },
        })

        # ── Validation ────────────────────────────────────────────
        yield _sse("stage_start", {"stage": "validation", "label": "Final Validation"})
        val_result = validate_app_config(app_config)
        graph = build_dependency_graph(app_config)
        broken_refs = graph.get_broken_refs()

        yield _sse("validation_complete", {
            "valid": val_result.valid,
            "errors": val_result.errors,
            "warnings": val_result.warnings,
            "broken_refs": broken_refs[:10],
            "semantic_valid": val_result.semantic_valid,
            "logic_valid": val_result.logic_valid,
        })

        # ── Execution ─────────────────────────────────────────────
        exec_report = None
        if request.run_execution_layer:
            yield _sse("stage_start", {"stage": "execution", "label": "Execution Simulation"})
            exec_report = await asyncio.to_thread(run_execution, app_config, run_id)
            yield _sse("execution_complete", {
                "executability_score": exec_report.executability_score,
                "db_tables_created": exec_report.db.tables_created,
                "db_tables_failed": exec_report.db.tables_failed,
                "api_success_rate": exec_report.api.success_rate,
                "ui_success_rate": exec_report.ui.success_rate,
                "report": exec_report.model_dump(),
            })

        # ── Final result ──────────────────────────────────────────
        total_ms = int((time.monotonic() - t_start) * 1000)
        yield _sse("pipeline_complete", {
            "run_id": run_id,
            "app_config": app_config.model_dump(),
            "validation": val_result.model_dump(),
            "execution_report": exec_report.model_dump() if exec_report else None,
            "stage_metrics": [m.model_dump() for m in all_metrics],
            "total_duration_ms": total_ms,
            "total_cost_usd": total_cost,
            "success": True,
        })

    except Exception as exc:
        logger.exception(f"[Pipeline] Fatal error in run {run_id}")
        yield _sse("pipeline_error", {
            "run_id": run_id,
            "error": str(exc),
            "success": False,
        })


# ── Routes ─────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/run")
async def run_pipeline(request: RunRequest):
    """Full pipeline with SSE streaming. Connect with EventSource."""
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt cannot be empty")
    return StreamingResponse(
        _run_pipeline_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/execute")
async def execute_config(request: ExecuteRequest):
    """Run only the execution/simulation layer on an existing AppConfig."""
    try:
        app_config = AppConfig.model_validate(request.app_config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    run_id = str(uuid.uuid4())[:8]
    report = await asyncio.to_thread(run_execution, app_config, run_id)
    return report.model_dump()


@app.post("/api/validate")
async def validate_config(request: ExecuteRequest):
    """Run 4-layer validation on an AppConfig."""
    try:
        app_config = AppConfig.model_validate(request.app_config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    val_result = validate_app_config(app_config)
    graph = build_dependency_graph(app_config)
    broken_refs = graph.get_broken_refs()

    return {
        "validation": val_result.model_dump(),
        "dependency_graph": {
            "total_nodes": len(graph.nodes),
            "broken_refs": broken_refs,
        },
    }


@app.get("/api/eval/results")
async def get_eval_results():
    """Return latest eval run results."""
    try:
        from eval.harness import get_latest_results
        return get_latest_results()
    except Exception as e:
        return {"error": str(e), "results": []}


@app.post("/api/eval/run")
async def trigger_eval(background_tasks: BackgroundTasks):
    """Trigger a background eval run over all 20 test prompts."""
    async def _run_eval():
        from eval.harness import run_eval_harness
        await run_eval_harness()

    background_tasks.add_task(_run_eval)
    return {"message": "Eval run started in background", "status": "running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
