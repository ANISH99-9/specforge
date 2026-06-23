"""
SpecForge — Eval Harness
Runs all 20 test prompts and captures metrics:
- Success rate, retries, latency, failure type breakdown
- Stability score (5 runs of same prompt, diff outputs)
- Executability score
- Cost per run
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sqlite3
import time
import hashlib
from datetime import datetime, timezone
from typing import Optional

from eval.test_prompts import ALL_PROMPTS

logger = logging.getLogger(__name__)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
DB_PATH     = os.path.join(os.path.dirname(__file__), "eval.db")


def _init_db() -> sqlite3.Connection:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            run_batch TEXT,
            prompt_id TEXT,
            category TEXT,
            label TEXT,
            prompt TEXT,
            success INTEGER,
            total_duration_ms INTEGER,
            total_cost_usd REAL,
            repair_iterations INTEGER,
            failure_type TEXT,
            executability_score REAL,
            confidence REAL,
            ambiguity_count INTEGER,
            assumption_count INTEGER,
            conflict_count INTEGER,
            result_json TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stability_runs (
            id TEXT PRIMARY KEY,
            prompt_id TEXT,
            run_index INTEGER,
            output_hash TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


async def _run_single(prompt_entry: dict, batch_id: str, conn: sqlite3.Connection) -> dict:
    """Run the full pipeline on one prompt and log results."""
    import uuid
    from pipeline.llm_client import get_llm_client
    from pipeline.intent import run_intent_extraction
    from pipeline.design import run_system_design
    from pipeline.schema import run_schema_generation
    from pipeline.refine import run_refinement
    from repair.repair_engine import RepairEngine
    from execution.executor import run_execution
    from config import settings

    run_id = str(uuid.uuid4())[:8]
    llm    = get_llm_client()
    repair = RepairEngine(llm, max_retries=settings.max_repair_retries)

    t0 = time.monotonic()
    total_cost = 0.0
    total_repairs = 0
    failure_type = None
    success = False
    exec_score = 0.0
    result_snapshot = {}

    try:
        intent, m1 = await run_intent_extraction(prompt_entry["prompt"], llm, repair)
        total_cost += m1.cost_usd
        total_repairs += m1.repair_iterations

        arch, m2 = await run_system_design(intent, llm, repair)
        total_cost += m2.cost_usd
        total_repairs += m2.repair_iterations

        ui, api, db, auth, ms3 = await run_schema_generation(intent, arch, llm, repair)
        for m in ms3:
            total_cost += m.cost_usd
            total_repairs += m.repair_iterations

        app_config, m4 = await run_refinement(
            intent, arch, ui, api, db, auth, run_id, llm, repair
        )
        total_cost += m4.cost_usd
        total_repairs += m4.repair_iterations

        exec_report = await asyncio.to_thread(run_execution, app_config, run_id)
        exec_score  = exec_report.executability_score
        success     = True

        result_snapshot = {
            "app_name":         intent.app_name,
            "app_type":         intent.app_type,
            "entities":         len(arch.entities),
            "pages":            len(arch.page_flow),
            "api_endpoints":    len(api.endpoints),
            "db_tables":        len(db.tables),
            "assumptions":      len(app_config.assumptions),
            "conflicts":        len(app_config.conflicts),
            "executability":    exec_score,
            "confidence":       intent.confidence,
            "ambiguities":      len(intent.ambiguities),
        }

    except Exception as exc:
        logger.error(f"[Eval] prompt={prompt_entry['id']} failed: {exc}")
        failure_type = type(exc).__name__
        result_snapshot = {"error": str(exc)}

    total_ms = int((time.monotonic() - t0) * 1000)

    row_id = f"{batch_id}_{prompt_entry['id']}"
    conn.execute("""
        INSERT OR REPLACE INTO eval_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        row_id, batch_id, prompt_entry["id"], prompt_entry["category"],
        prompt_entry["label"], prompt_entry["prompt"][:500],
        int(success), total_ms, total_cost,
        total_repairs, failure_type, exec_score,
        result_snapshot.get("confidence", 0),
        result_snapshot.get("ambiguities", 0),
        result_snapshot.get("assumptions", 0),
        result_snapshot.get("conflicts", 0),
        json.dumps(result_snapshot),
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()

    logger.info(
        f"[Eval] {prompt_entry['id']} {'✓' if success else '✗'} | "
        f"{total_ms}ms | ${total_cost:.4f} | exec={exec_score:.2f}"
    )
    return {"prompt_id": prompt_entry["id"], "success": success,
            "duration_ms": total_ms, "cost_usd": total_cost, **result_snapshot}


async def run_eval_harness(prompts=None, batch_id: Optional[str] = None) -> dict:
    """Run the full eval harness (or a subset of prompts)."""
    import uuid
    prompts   = prompts or ALL_PROMPTS
    batch_id  = batch_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    conn      = _init_db()
    results   = []

    logger.info(f"[Eval] Starting batch {batch_id} with {len(prompts)} prompts")

    # Run concurrently in small batches to avoid rate-limit
    batch_size = 3
    for i in range(0, len(prompts), batch_size):
        batch = prompts[i:i + batch_size]
        batch_results = await asyncio.gather(
            *[_run_single(p, batch_id, conn) for p in batch],
            return_exceptions=True,
        )
        for r in batch_results:
            if isinstance(r, Exception):
                logger.error(f"[Eval] Batch error: {r}")
            else:
                results.append(r)
        await asyncio.sleep(1)  # brief pause between batches

    summary = _compute_summary(results, batch_id)

    # Write markdown report
    _write_markdown_report(summary, results, batch_id)
    conn.close()

    return summary


def _compute_summary(results: list[dict], batch_id: str) -> dict:
    total = len(results)
    if total == 0:
        return {}

    success_count = sum(1 for r in results if r.get("success"))
    failed = [r for r in results if not r.get("success")]

    avg_duration = sum(r.get("duration_ms", 0) for r in results) / total
    avg_cost     = sum(r.get("cost_usd", 0) for r in results) / total
    total_cost   = sum(r.get("cost_usd", 0) for r in results)
    avg_exec     = sum(r.get("executability", 0) for r in results if r.get("success")) / max(success_count, 1)

    return {
        "batch_id":        batch_id,
        "total_prompts":   total,
        "success_count":   success_count,
        "success_rate":    round(success_count / total, 3),
        "avg_duration_ms": round(avg_duration),
        "avg_cost_usd":    round(avg_cost, 5),
        "total_cost_usd":  round(total_cost, 4),
        "avg_executability_score": round(avg_exec, 3),
        "failures": [{"id": r.get("prompt_id"), "error": r.get("error")} for r in failed],
    }


def _write_markdown_report(summary: dict, results: list[dict], batch_id: str) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"report_{batch_id}.md")

    lines = [
        f"# SpecForge Eval Report — {batch_id}",
        "",
        "## Summary",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total prompts | {summary.get('total_prompts')} |",
        f"| Success rate | {summary.get('success_rate', 0):.1%} |",
        f"| Avg duration | {summary.get('avg_duration_ms')}ms |",
        f"| Avg cost/run | ${summary.get('avg_cost_usd', 0):.4f} |",
        f"| Total cost | ${summary.get('total_cost_usd', 0):.4f} |",
        f"| Avg executability | {summary.get('avg_executability_score', 0):.1%} |",
        "",
        "## Per-Prompt Results",
        "| ID | Category | Label | Success | Duration | Cost | Exec Score |",
        "|----|----------|-------|---------|----------|------|------------|",
    ]

    for r in results:
        lines.append(
            f"| {r.get('prompt_id','')} | {r.get('category','')} | "
            f"{r.get('label','')} | {'✓' if r.get('success') else '✗'} | "
            f"{r.get('duration_ms',0)}ms | ${r.get('cost_usd',0):.4f} | "
            f"{r.get('executability', 0):.1%} |"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"[Eval] Report written to {path}")


def get_latest_results() -> dict:
    """Return latest eval results from the SQLite db."""
    if not os.path.exists(DB_PATH):
        return {"results": [], "summary": {}}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT * FROM eval_runs
        ORDER BY created_at DESC
        LIMIT 60
    """).fetchall()

    conn.close()

    results = [dict(r) for r in rows]

    # Compute summary on the fly
    total = len(results)
    success_count = sum(1 for r in results if r.get("success"))

    return {
        "results": results,
        "summary": {
            "total": total,
            "success_count": success_count,
            "success_rate": success_count / total if total else 0,
            "avg_duration_ms": sum(r.get("total_duration_ms", 0) for r in results) / max(total, 1),
            "avg_cost_usd": sum(r.get("total_cost_usd", 0) for r in results) / max(total, 1),
            "avg_executability": sum(r.get("executability_score", 0) for r in results) / max(total, 1),
        },
    }
