"""
SpecForge — Stage 4: Refinement / Cross-layer Consistency
Provider: Google Gemini 2.5 Flash (fallback: Groq)

Flow:
  Validation Failure → Identify Broken Component → Send Only Broken Parts → Repair → Re-validate

LLM receives ONLY:
  - validation issues
  - failed endpoints
  - missing fields
"""
from __future__ import annotations
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from validation.schema_defs import (
    IntentSpec, ArchitectureSpec,
    UISchema, APISchema, DBSchema, AuthSchema,
    AppConfig, Assumption, Conflict, StageMetrics,
)
from validation.validator import (
    validate_stage_output, auto_fix_json,
    validate_semantic, validate_logic,
)
from repair.repair_engine import RepairEngine, apply_stage_auto_fixes, auto_fix_app_config
from pipeline.llm_client import LLMClient
from config import settings

logger = logging.getLogger(__name__)

REFINEMENT_MAX_TOKENS = 1000

SYSTEM_PROMPT = """You are SpecForge's Refinement Repair Engine.

You receive ONLY:
1. validation_issues — cross-layer errors found during validation
2. failed_endpoints — API endpoint objects that failed validation
3. missing_fields — field paths that are missing or invalid

Your job: return targeted fixes ONLY. Do NOT return full schemas.

Return JSON with this exact shape:
{
  "endpoint_fixes": [
    { "id": "GET_users", "method": "GET", "path": "/api/users", ... }
  ],
  "assumptions": [
    {
      "field": "payment_provider",
      "assumed_value": "Stripe",
      "reason": "Not specified; Stripe is the default",
      "can_override": true,
      "stage": "refinement"
    }
  ],
  "conflicts": [
    {
      "description": "UI bound to missing endpoint",
      "resolution": "Remapped to GET_users",
      "source": "Stage 4 refinement",
      "severity": "warning"
    }
  ]
}

Rules:
1. endpoint_fixes: return ONLY corrected endpoint objects (not the full APISchema)
2. response_fields MUST be non-empty (min 1 field per endpoint)
3. Keep endpoint fixes concise — max 5 response_fields per endpoint
4. Return ONLY valid JSON, no markdown"""

USER_TEMPLATE = """Fix these validation failures.

VALIDATION ISSUES:
{validation_issues}

FAILED ENDPOINTS:
{failed_endpoints}

MISSING FIELDS:
{missing_fields}

Return targeted fixes JSON:"""


def _detect_local_conflicts(
    intent: IntentSpec,
    arch: ArchitectureSpec,
    ui: UISchema,
    api: APISchema,
    db: DBSchema,
    auth: AuthSchema,
) -> list[dict]:
    """Deterministic local conflict detection before calling LLM."""
    conflicts = []
    api_ids = {ep.id for ep in api.endpoints}
    db_tables = {t.name for t in db.tables}

    def _check(comp):
        if comp.data_binding and comp.data_binding.endpoint_id not in api_ids:
            conflicts.append({
                "description": (
                    f"UI '{comp.id}' binds to unknown endpoint "
                    f"'{comp.data_binding.endpoint_id}'"
                ),
                "resolution": "LLM will remap to closest endpoint",
                "source": "Stage 4 pre-check",
                "severity": "warning",
            })
        for child in (comp.children or []):
            _check(child)

    for page in ui.pages:
        _check(page)

    for ep in api.endpoints:
        for tbl in ep.db_tables:
            if tbl not in db_tables:
                conflicts.append({
                    "description": f"API '{ep.id}' references missing table '{tbl}'",
                    "resolution": "LLM will add the missing table to DB schema",
                    "source": "Stage 4 pre-check",
                    "severity": "error",
                })

    return conflicts


def _collect_refinement_issues(
    intent: IntentSpec,
    arch: ArchitectureSpec,
    ui: UISchema,
    api: APISchema,
    db: DBSchema,
    auth: AuthSchema,
    run_id: str,
) -> dict[str, Any]:
    """
    Gather validation issues, failed endpoints, and missing fields.
    Only these are sent to the LLM.
    """
    validation_issues: list[str] = []
    missing_fields: list[str] = []
    failed_endpoint_ids: set[str] = set()

    for stage, data in [
        ("intent", intent.model_dump()),
        ("design", arch.model_dump()),
        ("ui_schema", ui.model_dump()),
        ("api_schema", api.model_dump()),
        ("db_schema", db.model_dump()),
        ("auth_schema", auth.model_dump()),
    ]:
        result = validate_stage_output(stage, data)
        if not result.valid:
            validation_issues.extend(result.errors)
            missing_fields.extend(result.missing_fields)

    local_conflicts = _detect_local_conflicts(intent, arch, ui, api, db, auth)
    for c in local_conflicts:
        validation_issues.append(c["description"])

    draft = AppConfig(
        intent=intent,
        architecture=arch,
        ui_schema=ui,
        api_schema=api,
        db_schema=db,
        auth_schema=auth,
        assumptions=[],
        conflicts=[Conflict(**c) for c in local_conflicts],
        generated_at=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        pipeline_version="1.0.0",
    )
    draft = auto_fix_app_config(draft)

    sem_ok, sem_errs, sem_warns = validate_semantic(draft)
    validation_issues.extend(sem_errs)
    validation_issues.extend(sem_warns)

    log_ok, log_errs = validate_logic(draft)
    validation_issues.extend(log_errs)

    api_by_id = {ep.id: ep for ep in api.endpoints}
    for err in validation_issues:
        m = re.search(r"API endpoint '([^']+)'", err)
        if m:
            failed_endpoint_ids.add(m.group(1))
        m = re.search(r"endpoints\.(\d+)\.", err)
        if m:
            idx = int(m.group(1))
            if 0 <= idx < len(api.endpoints):
                failed_endpoint_ids.add(api.endpoints[idx].id)

    failed_endpoints = [
        api_by_id[eid].model_dump()
        for eid in failed_endpoint_ids
        if eid in api_by_id
    ]

    return {
        "validation_issues": validation_issues[:20],
        "failed_endpoints": failed_endpoints,
        "missing_fields": missing_fields[:20],
        "local_conflicts": local_conflicts,
    }


def _apply_refinement_patches(
    app_config: AppConfig,
    patches: dict,
) -> AppConfig:
    """Merge targeted LLM fixes into the existing AppConfig."""
    endpoint_fixes = patches.get("endpoint_fixes") or []
    if endpoint_fixes:
        fix_by_id = {
            fix["id"]: fix
            for fix in endpoint_fixes
            if isinstance(fix, dict) and fix.get("id")
        }
        updated_endpoints = []
        for ep in app_config.api_schema.endpoints:
            if ep.id in fix_by_id:
                updated_endpoints.append(fix_by_id[ep.id])
            else:
                updated_endpoints.append(ep.model_dump())
        api_data = apply_stage_auto_fixes(
            "api_schema",
            {
                "base_path": app_config.api_schema.base_path,
                "endpoints": updated_endpoints,
            },
        )
        app_config.api_schema = APISchema.model_validate(api_data)

    for a in patches.get("assumptions") or []:
        if isinstance(a, dict):
            app_config.assumptions.append(Assumption(**a))

    existing_descs = {c.description for c in app_config.conflicts}
    for c in patches.get("conflicts") or []:
        if isinstance(c, dict) and c.get("description") not in existing_descs:
            app_config.conflicts.append(Conflict(**c))

    return app_config


def _sanitize_ui_bindings(app_config: AppConfig) -> None:
    """Programmatic UI binding repair (no LLM)."""
    api_ids = {ep.id for ep in app_config.api_schema.endpoints}

    def _sanitize_comp(comp):
        if comp.data_binding and comp.data_binding.endpoint_id not in api_ids:
            matching = [
                aid for aid in api_ids
                if comp.data_binding.endpoint_id.split("_")[-1].lower() in aid.lower()
                or comp.id.split("_")[0].lower() in aid.lower()
            ]
            if matching:
                comp.data_binding.endpoint_id = matching[0]
            else:
                fallback = [
                    aid for aid in api_ids
                    if aid.startswith("GET_") or "list" in aid.lower()
                ]
                if fallback:
                    comp.data_binding.endpoint_id = fallback[0]
        if comp.actions:
            comp.actions = [act for act in comp.actions if act in api_ids]
        for child in (comp.children or []):
            _sanitize_comp(child)

    for page in app_config.ui_schema.pages:
        _sanitize_comp(page)


async def run_refinement(
    intent: IntentSpec,
    arch: ArchitectureSpec,
    ui: UISchema,
    api: APISchema,
    db: DBSchema,
    auth: AuthSchema,
    run_id: str,
    llm: LLMClient,
    repair_engine: RepairEngine,
) -> tuple[AppConfig, StageMetrics]:
    """Stage 4: validate → identify broken parts → targeted LLM repair → re-validate."""
    t0 = time.monotonic()
    provider = settings.stage4_provider
    model = settings.stage4_model

    issues = _collect_refinement_issues(intent, arch, ui, api, db, auth, run_id)

    app_config = AppConfig(
        intent=intent,
        architecture=arch,
        ui_schema=ui,
        api_schema=api,
        db_schema=db,
        auth_schema=auth,
        assumptions=_infer_assumptions(intent),
        conflicts=[Conflict(**c) for c in issues["local_conflicts"]],
        generated_at=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        pipeline_version="1.0.0",
    )

    # Fix cross-layer issues before targeted LLM repair
    app_config = auto_fix_app_config(app_config)

    repair_iterations = 0
    meta_input = meta_output = 0
    meta_cost = 0.0

    has_issues = (
        issues["validation_issues"]
        or issues["failed_endpoints"]
        or issues["missing_fields"]
    )

    if has_issues:
        logger.info(
            f"[Stage 4] Sending targeted repair: "
            f"{len(issues['validation_issues'])} issues, "
            f"{len(issues['failed_endpoints'])} failed endpoints, "
            f"{len(issues['missing_fields'])} missing fields"
        )

        raw, meta = await llm.complete_raw(
            provider=provider,
            model=model,
            system=SYSTEM_PROMPT,
            user=USER_TEMPLATE.format(
                validation_issues="\n".join(f"- {e}" for e in issues["validation_issues"])
                or "(none)",
                failed_endpoints=json.dumps(issues["failed_endpoints"], indent=2)[:3000]
                or "[]",
                missing_fields="\n".join(f"- {f}" for f in issues["missing_fields"])
                or "(none)",
            ),
            temperature=0.0,
            return_meta=True,
            max_tokens=REFINEMENT_MAX_TOKENS,
        )
        repair_iterations = 1
        meta_input = meta.input_tokens
        meta_output = meta.output_tokens
        meta_cost = meta.cost_usd

        try:
            patches = json.loads(auto_fix_json(raw))
            if isinstance(patches, dict):
                app_config = _apply_refinement_patches(app_config, patches)
        except Exception as e:
            logger.warning(f"[Stage 4] Failed to apply LLM patches: {e}")

    _sanitize_ui_bindings(app_config)

    # Auto-fix cross-layer issues (missing DB tables, undefined roles)
    app_config = auto_fix_app_config(app_config)

    sem_ok, sem_errs, _ = validate_semantic(app_config)
    log_ok, log_errs = validate_logic(app_config)
    if not sem_ok:
        logger.warning(f"[Stage 4] Remaining semantic errors after auto-fix: {sem_errs[:3]}")
    if not log_ok:
        logger.warning(f"[Stage 4] Remaining logic errors after auto-fix: {log_errs[:3]}")

    duration_ms = int((time.monotonic() - t0) * 1000)

    stage_metrics = StageMetrics(
        stage="refinement",
        provider=provider,
        model=model,
        duration_ms=duration_ms,
        input_tokens=meta_input,
        output_tokens=meta_output,
        cost_usd=meta_cost,
        repair_iterations=repair_iterations,
        success=True,
    )

    logger.info(
        f"[Stage 4] Done | assumptions={len(app_config.assumptions)} "
        f"| conflicts={len(app_config.conflicts)} | llm_called={has_issues} | {duration_ms}ms"
    )
    return app_config, stage_metrics


def _infer_assumptions(intent: IntentSpec) -> list[Assumption]:
    """Heuristic assumptions based on IntentSpec ambiguities."""
    assumptions = []

    if intent.monetization.has_premium_plan and not intent.monetization.gating_hint:
        assumptions.append(Assumption(
            field="payment_provider",
            assumed_value="Stripe",
            reason="Payment provider not specified; Stripe is the industry default",
            can_override=True,
            stage="refinement",
        ))

    for amb in intent.ambiguities:
        lower = amb.lower()
        if "payment" in lower or "pricing" in lower:
            assumptions.append(Assumption(
                field="pricing_tier",
                assumed_value="Free + Pro ($9.99/mo)",
                reason=f"Ambiguity detected: '{amb}' — using a sensible default",
                can_override=True,
                stage="refinement",
            ))
        if "provider" in lower and "auth" in lower:
            assumptions.append(Assumption(
                field="auth_provider",
                assumed_value="email/password",
                reason="Auth provider unspecified — defaulted to email/password",
                can_override=True,
                stage="refinement",
            ))

    return assumptions
