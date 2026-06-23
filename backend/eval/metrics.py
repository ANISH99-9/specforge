"""
SpecForge — Metrics Computation
Standalone functions for computing eval metrics from raw results.
"""
from __future__ import annotations
from typing import Any


def compute_success_rate(results: list[dict]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.get("success")) / len(results)


def compute_stability_score(runs: list[dict]) -> float:
    """
    Given N runs of the same prompt, compute a stability score (0-1).
    Score = 1.0 means all outputs identical; 0.0 means all different.
    Based on field-level comparison of key AppConfig fields.
    """
    if len(runs) < 2:
        return 1.0
    fingerprints = []
    for r in runs:
        cfg = r.get("app_config", {})
        intent = cfg.get("intent", {})
        fp = (
            intent.get("app_type", ""),
            intent.get("app_name", ""),
            len(cfg.get("db_schema", {}).get("tables", [])),
            len(cfg.get("api_schema", {}).get("endpoints", [])),
            len(cfg.get("ui_schema", {}).get("pages", [])),
        )
        fingerprints.append(fp)
    # Count pairs that match
    matches = sum(
        1 for i in range(len(fingerprints))
        for j in range(i + 1, len(fingerprints))
        if fingerprints[i] == fingerprints[j]
    )
    total_pairs = len(fingerprints) * (len(fingerprints) - 1) // 2
    return matches / total_pairs if total_pairs > 0 else 1.0


def compute_cost_estimate(stage_metrics: list[dict]) -> float:
    return sum(m.get("cost_usd", 0.0) for m in stage_metrics)


def compute_failure_breakdown(results: list[dict]) -> dict[str, int]:
    breakdown: dict[str, int] = {
        "syntax": 0,
        "missing_field": 0,
        "hallucinated_field": 0,
        "cross_layer_mismatch": 0,
        "logical_conflict": 0,
        "unknown": 0,
    }
    for r in results:
        if not r.get("success"):
            ft = r.get("failure_type", "unknown") or "unknown"
            key = ft.lower().replace(" ", "_")
            if key in breakdown:
                breakdown[key] += 1
            else:
                breakdown["unknown"] += 1
    return breakdown


def compute_latency_percentiles(results: list[dict]) -> dict[str, float]:
    durations = sorted(r.get("duration_ms", 0) for r in results if r.get("success"))
    if not durations:
        return {"p50": 0, "p90": 0, "min": 0, "max": 0}
    n = len(durations)
    return {
        "p50": durations[int(n * 0.5)],
        "p90": durations[int(n * 0.9)],
        "min": durations[0],
        "max": durations[-1],
    }


def build_summary_table(results: list[dict]) -> str:
    """Build a markdown table from results list."""
    rows = [
        "| ID | Category | Success | Duration (s) | Cost ($) | Exec Score |",
        "|----|----------|---------|--------------|----------|------------|",
    ]
    for r in results:
        rows.append(
            f"| {r.get('prompt_id','?')} "
            f"| {r.get('category','?')} "
            f"| {'✓' if r.get('success') else '✗'} "
            f"| {r.get('duration_ms',0)/1000:.1f} "
            f"| {r.get('cost_usd',0):.4f} "
            f"| {r.get('executability', 0):.1%} |"
        )
    return "\n".join(rows)
