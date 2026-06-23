"""
SpecForge — Stage 1: Intent Extraction
Provider: Google Gemini 2.5 Flash (fallback: OpenRouter → Groq)
Output:   IntentSpec
"""
from __future__ import annotations
import json
import logging
from validation.schema_defs import IntentSpec, StageMetrics
from validation.validator import validate_stage_output
from repair.repair_engine import RepairEngine
from pipeline.llm_client import LLMClient
from config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are SpecForge's Intent Extraction Engine — a senior software architect's structured parsing system.

Your ONLY job: analyse a natural-language app description and return a single, complete IntentSpec JSON object.

STRICT RULES:
1. Return ONLY valid JSON — no markdown fences, no explanation, no preamble
2. Temperature = 0 — be deterministic and precise
3. app_type MUST be one of: CRM, Marketplace, SaaS Dashboard, E-commerce, Booking Platform, Blog/CMS, Analytics Tool, LMS, Social Platform, Project Management, Inventory System, HR Tool, Healthcare, FinTech, Other
4. entities: list every distinct data entity implied (min 2)
5. ambiguities[]: only items that genuinely affect technical design decisions
6. confidence: 0.0=completely vague, 1.0=fully specified. Be honest and calibrated.
7. roles[]: every distinct user role implied (min 1, usually 2-3)
8. features[]: concrete feature names, not vague descriptions

Required JSON shape (return ALL fields):
{
  "app_name": "string",
  "app_type": "one of the allowed types above",
  "entities": [{"name": "PascalCase", "fields_hint": ["field1", "field2"]}],
  "roles": ["Admin", "User"],
  "features": ["feature_name_1", "feature_name_2"],
  "monetization": {"has_premium_plan": false, "gating_hint": null},
  "ambiguities": ["description of genuine ambiguity"],
  "confidence": 0.85
}"""

USER_TEMPLATE = """Analyse this app description and return the IntentSpec JSON:

\"\"\"{prompt}\"\"\"

Return ONLY the JSON object:"""


async def run_intent_extraction(
    prompt: str,
    llm: LLMClient,
    repair_engine: RepairEngine,
) -> tuple[IntentSpec, StageMetrics]:
    """
    Stage 1: Extract structured intent from raw user prompt.
    Returns (IntentSpec, StageMetrics).
    """
    import time
    t0 = time.monotonic()

    provider = settings.stage1_provider
    model    = settings.stage1_model

    # LLM call
    raw, meta = await llm.complete_raw(
        provider=provider,
        model=model,
        system=SYSTEM_PROMPT,
        user=USER_TEMPLATE.format(prompt=prompt),
        temperature=0.0,
        return_meta=True,
    )

    # Validate
    val_result = validate_stage_output("intent", raw)
    repair_iterations = 0

    if not val_result.valid:
        logger.warning(f"[Stage 1] Validation failed: {val_result.errors[:3]}")
        repair_result = await repair_engine.repair(
            raw_output=raw,
            validation_result=val_result,
            stage="intent",
            context={"prompt": prompt},
        )
        repair_iterations = repair_result.iterations

        if repair_result.success and repair_result.patched_data:
            intent = IntentSpec.model_validate(repair_result.patched_data)
        else:
            # Best-effort fallback
            logger.error(f"[Stage 1] Repair escalated: {repair_result.error_message}")
            intent = _fallback_intent(prompt)
    else:
        import json as _json
        from validation.validator import auto_fix_json
        intent = IntentSpec.model_validate(_json.loads(auto_fix_json(raw)))

    duration_ms = int((time.monotonic() - t0) * 1000)

    stage_metrics = StageMetrics(
        stage="intent",
        provider=provider,
        model=model,
        duration_ms=duration_ms,
        input_tokens=meta.input_tokens,
        output_tokens=meta.output_tokens,
        cost_usd=meta.cost_usd,
        repair_iterations=repair_iterations,
        success=True,
    )

    logger.info(
        f"[Stage 1] Done | confidence={intent.confidence:.2f} "
        f"| ambiguities={len(intent.ambiguities)} | {duration_ms}ms"
    )
    return intent, stage_metrics


def _fallback_intent(prompt: str) -> IntentSpec:
    """Minimal valid IntentSpec when LLM + repair both fail."""
    from validation.schema_defs import EntityHint, MonetizationSpec
    return IntentSpec(
        app_name="Unnamed App",
        app_type="Other",
        entities=[EntityHint(name="User", fields_hint=["name", "email"])],
        roles=["Admin", "User"],
        features=["authentication", "dashboard"],
        monetization=MonetizationSpec(has_premium_plan=False),
        ambiguities=["Could not fully parse the prompt — please be more specific"],
        confidence=0.1,
    )
