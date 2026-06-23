"""
SpecForge — Stage 2: System Design Layer
Provider: Google Gemini 2.5 Flash (fallback: OpenRouter → Groq)
Input:    IntentSpec
Output:   ArchitectureSpec
"""
from __future__ import annotations
import json
import logging
import time
from validation.schema_defs import IntentSpec, ArchitectureSpec, StageMetrics
from validation.validator import validate_stage_output, auto_fix_json
from repair.repair_engine import RepairEngine
from pipeline.llm_client import LLMClient
from config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are SpecForge's System Design Engine.

Given an IntentSpec JSON, produce a complete ArchitectureSpec with:
- Detailed entity schemas with field types and relationships
- Full permission matrix (every role × action × entity combination)
- Page/flow map with paths and role access
- Business rules as structured objects

STRICT RULES:
1. Return ONLY valid JSON — no markdown, no explanation
2. Temperature = 0
3. Every entity MUST have at least: id (uuid), created_at (datetime), updated_at (datetime)
4. Every entity field type MUST be exactly one of these literal values: "string", "number", "boolean", "datetime", "enum", "relation", "text", "uuid". Do NOT output any other types.
5. relation fields must specify relation_to (entity name) and relation_type (1:1, 1:N, or N:N)
6. permission_matrix must cover ALL combinations of roles × actions × entities
7. page_flow must include login page + at least one page per major entity
8. business_rules must capture any premium/gating/conditional logic from monetization

Required JSON shape:
{
  "entities": [
    {
      "name": "Contact",
      "fields": [
        {"name": "id", "type": "uuid", "required": true},
        {"name": "name", "type": "string", "required": true},
        {"name": "email", "type": "string", "required": true},
        {"name": "owner_id", "type": "uuid", "required": true, "relation_to": "User", "relation_type": "1:N"}
      ]
    }
  ],
  "permission_matrix": [
    {"role": "Admin", "action": "create", "entity": "Contact", "allowed": true},
    {"role": "User", "action": "read", "entity": "Contact", "allowed": true, "condition": "user.id == resource.owner_id"}
  ],
  "page_flow": [
    {"name": "Login", "path": "/login", "roles_allowed": ["Admin", "User"]},
    {"name": "Dashboard", "path": "/dashboard", "roles_allowed": ["Admin", "User"]}
  ],
  "business_rules": [
    {"rule": "premium_gating", "applies_to": "analytics page", "condition": "user.plan == premium"}
  ]
}"""

USER_TEMPLATE = """IntentSpec to convert into ArchitectureSpec:

{intent_json}

Return ONLY the ArchitectureSpec JSON:"""


async def run_system_design(
    intent: IntentSpec,
    llm: LLMClient,
    repair_engine: RepairEngine,
) -> tuple[ArchitectureSpec, StageMetrics]:
    """Stage 2: Produce full architecture from IntentSpec."""
    t0 = time.monotonic()

    provider = settings.stage2_provider
    model    = settings.stage2_model

    intent_json = intent.model_dump_json(indent=2)

    raw, meta = await llm.complete_raw(
        provider=provider,
        model=model,
        system=SYSTEM_PROMPT,
        user=USER_TEMPLATE.format(intent_json=intent_json),
        temperature=0.0,
        return_meta=True,
    )

    val_result = validate_stage_output("design", raw)
    repair_iterations = 0

    if not val_result.valid:
        logger.warning(f"[Stage 2] Validation failed: {val_result.errors[:3]}")
        repair_result = await repair_engine.repair(
            raw_output=raw,
            validation_result=val_result,
            stage="design",
            context={"intent": intent.model_dump()},
        )
        repair_iterations = repair_result.iterations
        if repair_result.success and repair_result.patched_data:
            arch = ArchitectureSpec.model_validate(repair_result.patched_data)
        else:
            logger.error(f"[Stage 2] Repair escalated: {repair_result.error_message}")
            arch = _fallback_arch(intent)
    else:
        arch = ArchitectureSpec.model_validate(json.loads(auto_fix_json(raw)))

    duration_ms = int((time.monotonic() - t0) * 1000)

    stage_metrics = StageMetrics(
        stage="design",
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
        f"[Stage 2] Done | entities={len(arch.entities)} "
        f"| pages={len(arch.page_flow)} | {duration_ms}ms"
    )
    return arch, stage_metrics


def _fallback_arch(intent: IntentSpec) -> ArchitectureSpec:
    from validation.schema_defs import (
        ArchEntity, EntityField, PermissionRule, Page, BusinessRule
    )
    entities = []
    for e in intent.entities:
        fields = [
            EntityField(name="id", type="uuid", required=True),
            EntityField(name="created_at", type="datetime", required=True),
        ]
        for f in e.fields_hint:
            fields.append(EntityField(name=f, type="string", required=True))
        entities.append(ArchEntity(name=e.name, fields=fields))

    perms = []
    for role in intent.roles:
        for entity in intent.entities:
            for action in ["create", "read", "update", "delete", "list"]:
                perms.append(PermissionRule(
                    role=role, action=action, entity=entity.name, allowed=True
                ))

    pages = [
        Page(name="Login", path="/login", roles_allowed=intent.roles),
        Page(name="Dashboard", path="/dashboard", roles_allowed=intent.roles),
    ]

    return ArchitectureSpec(
        entities=entities,
        permission_matrix=perms,
        page_flow=pages,
        business_rules=[],
    )
