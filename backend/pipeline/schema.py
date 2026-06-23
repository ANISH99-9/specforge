"""
SpecForge — Stage 3: Schema Generation
Provider: Google Gemini 2.5 Flash (fallback: OpenRouter → Groq)
Runs 4 PARALLEL LLM calls (UI / API / DB / Auth schemas).
Each is independently validatable and re-generatable.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from validation.schema_defs import (
    IntentSpec, ArchitectureSpec,
    UISchema, APISchema, DBSchema, AuthSchema,
    StageMetrics,
)
from validation.validator import validate_stage_output, auto_fix_json
from repair.repair_engine import (
    RepairEngine, apply_stage_auto_fixes, build_api_schema_from_arch,
)
from validation.validator import salvage_truncated_json
from pipeline.llm_client import LLMClient
from config import settings

logger = logging.getLogger(__name__)

# ── System prompts ────────────────────────────────────────────────

UI_SYSTEM = """You are SpecForge's UI Schema Generator.
Produce a UISchema JSON describing the complete component tree for the application.

RULES:
1. Return ONLY valid JSON.
2. Every page is a UIComponent with type="page".
3. Each component has a unique "id" (snake_case), a "type", and optional children[].
4. Component "type" MUST be exactly one of these literals: "page", "section", "card", "table", "form", "button", "chart", "badge", "nav", "sidebar", "modal", "input", "select".
   - DO NOT hallucinate other types like "table_column", "list", "list_item", "text", "metric_display", etc. Use "section", "card", "table", "form", "input", "select", or "button" instead.
5. If data_binding is present, data_binding.endpoint_id MUST exactly match a unique endpoint id from the API schema (e.g. GET_contacts).
6. actions[] contains endpoint ids this component calls (e.g. ["POST_contacts"]).
7. roles_visible restricts which roles see the component.

Required shape:
{
  "pages": [
    {
      "id": "login_page",
      "type": "page",
      "label": "Login",
      "children": [
        {
          "id": "login_form",
          "type": "form",
          "label": "Login Form",
          "actions": ["POST_auth_login"],
          "children": [
            {"id": "email_input", "type": "input", "label": "Email"},
            {"id": "password_input", "type": "input", "label": "Password"},
            {"id": "login_button", "type": "button", "label": "Sign In"}
          ]
        }
      ]
    }
  ]
}"""

API_SYSTEM = """You are SpecForge's API Schema Generator.
Produce an APISchema JSON describing all REST endpoints.

RULES:
1. Return ONLY valid JSON.
2. Every endpoint has a unique "id" (format: METHOD_resource, e.g. "GET_users", "POST_contacts"). Keep total endpoints under 10 for large apps.
3. db_tables[] lists every DB table the endpoint reads from or writes to (required).
4. For request_body, query_params, path_params, and response_fields, each field's "type" MUST be exactly one of these literal values: "string", "number", "boolean", "object", "array", "uuid", "datetime", "enum".
   - DO NOT output "text" (use "string").
   - DO NOT output "date" or "timestamp" (use "datetime").
5. response_fields[] must describe the key response shape. Max 3 response fields per endpoint to prevent JSON truncation.
6. roles_allowed[] must use exact role names from the architecture.
7. Include CRUD endpoints for every entity plus auth endpoints.

Required shape:
{
  "base_path": "/api",
  "endpoints": [
    {
      "id": "POST_auth_login",
      "method": "POST",
      "path": "/api/auth/login",
      "description": "Authenticate user and return JWT",
      "request_body": [
        {"name": "email", "type": "string", "required": true},
        {"name": "password", "type": "string", "required": true}
      ],
      "response_fields": [
        {"name": "token", "type": "string", "required": true},
        {"name": "user", "type": "object", "required": true}
      ],
      "auth_required": false,
      "roles_allowed": ["Admin", "User"],
      "db_tables": ["users"]
    }
  ]
}"""

DB_SYSTEM = """You are SpecForge's DB Schema Generator.
Produce a DBSchema JSON with SQLite-compatible table definitions.

RULES:
1. Return ONLY valid JSON
2. Every table MUST have an "id" column (type: UUID, primary_key: true)
3. Include created_at and updated_at TIMESTAMP columns on every table
4. Foreign keys: use "foreign_key": "other_table.id" format
5. Add indexes on foreign key columns and frequently-queried columns
6. Use SQLite-compatible types: TEXT, INTEGER, REAL, BLOB, BOOLEAN, TIMESTAMP, UUID, JSON

Required shape:
{
  "tables": [
    {
      "name": "users",
      "columns": [
        {"name": "id", "type": "UUID", "primary_key": true, "nullable": false},
        {"name": "email", "type": "TEXT", "nullable": false, "unique": true},
        {"name": "role", "type": "TEXT", "nullable": false, "default": "user"},
        {"name": "created_at", "type": "TIMESTAMP", "nullable": false, "default": "CURRENT_TIMESTAMP"},
        {"name": "updated_at", "type": "TIMESTAMP", "nullable": false, "default": "CURRENT_TIMESTAMP"}
      ],
      "indexes": [
        {"name": "idx_users_email", "columns": ["email"], "unique": true}
      ]
    }
  ]
}"""

AUTH_SYSTEM = """You are SpecForge's Auth Schema Generator.
Produce an AuthSchema JSON describing authentication and authorization.

RULES:
1. Return ONLY valid JSON
2. roles[] must exactly match roles from the architecture
3. permissions[] reproduces the permission matrix with role/action/entity triples
4. route_guards[] must protect every API path pattern requiring auth
5. session_type: "jwt" is default unless explicitly requested otherwise

Required shape:
{
  "roles": ["Admin", "User"],
  "permissions": [
    {"role": "Admin", "action": "create", "entity": "Contact", "allowed": true}
  ],
  "route_guards": [
    {"path_pattern": "/api/admin/*", "roles_allowed": ["Admin"], "redirect_to": "/login"},
    {"path_pattern": "/api/*", "roles_allowed": ["Admin", "User"], "redirect_to": "/login"}
  ],
  "session_type": "jwt",
  "providers": ["email"]
}"""

SCHEMA_USER_TEMPLATE = """Architecture specification:
{arch_json}

Intent specification:
{intent_json}

Generate the {schema_type} JSON now:"""


SCHEMA_MAX_TOKENS = 4096  # Gemini — larger budget for schema generation


async def _generate_one_schema(
    schema_type: str,
    system: str,
    intent: IntentSpec,
    arch: ArchitectureSpec,
    llm: LLMClient,
    repair_engine: RepairEngine,
    provider: str,
    model: str,
) -> tuple[dict, StageMetrics]:
    """Generate and validate a single sub-schema."""
    t0 = time.monotonic()

    arch_json   = arch.model_dump_json(indent=2)
    intent_json = intent.model_dump_json(indent=2)

    # Trim context for very long apps — architecture entities are the priority
    entity_count = len(arch.entities)
    arch_limit = 3000 if entity_count > 5 else 4000
    intent_limit = 1500 if entity_count > 5 else 2000

    raw, meta = await llm.complete_raw(
        provider=provider,
        model=model,
        system=system,
        user=SCHEMA_USER_TEMPLATE.format(
            arch_json=arch_json[:arch_limit],
            intent_json=intent_json[:intent_limit],
            schema_type=schema_type,
        ),
        temperature=0.0,
        return_meta=True,
        max_tokens=SCHEMA_MAX_TOKENS,
    )

    stage_key = schema_type
    val_result = validate_stage_output(stage_key, raw)
    repair_iterations = 0

    if not val_result.valid:
        logger.warning(f"[Stage 3/{schema_type}] Validation failed: {val_result.errors[:3]}")
        repair_result = await repair_engine.repair(
            raw_output=raw,
            validation_result=val_result,
            stage=stage_key,
            context={"arch": arch.model_dump(), "intent": intent.model_dump()},
        )
        repair_iterations = repair_result.iterations
        if repair_result.success and repair_result.patched_data:
            data = repair_result.patched_data
        else:
            logger.warning(f"[Stage 3/{schema_type}] Repair escalated — using salvage/fallback")
            data = salvage_truncated_json(raw, stage_key)
            if data is None and stage_key == "api_schema":
                data = build_api_schema_from_arch(
                    arch.model_dump(), intent.model_dump()
                )
            elif data is None:
                try:
                    data = json.loads(auto_fix_json(raw))
                except json.JSONDecodeError:
                    data = {}
            data = apply_stage_auto_fixes(stage_key, data or {})
    else:
        data = json.loads(auto_fix_json(raw))
        data = apply_stage_auto_fixes(stage_key, data)

    duration_ms = int((time.monotonic() - t0) * 1000)
    stage_metrics = StageMetrics(
        stage=f"schema_{schema_type}",
        provider=provider,
        model=model,
        duration_ms=duration_ms,
        input_tokens=meta.input_tokens,
        output_tokens=meta.output_tokens,
        cost_usd=meta.cost_usd,
        repair_iterations=repair_iterations,
        success=True,
    )

    return data, stage_metrics


async def run_schema_generation(
    intent: IntentSpec,
    arch: ArchitectureSpec,
    llm: LLMClient,
    repair_engine: RepairEngine,
) -> tuple[UISchema, APISchema, DBSchema, AuthSchema, list[StageMetrics]]:
    """
    Stage 3: Generate 4 sub-schemas IN PARALLEL.
    Each is a separate LLM call → independently re-generatable.
    """
    provider = settings.stage3_provider
    model    = settings.stage3_model

    # Fire all 4 calls concurrently
    results = await asyncio.gather(
        _generate_one_schema("ui_schema",   UI_SYSTEM,   intent, arch, llm, repair_engine, provider, model),
        _generate_one_schema("api_schema",  API_SYSTEM,  intent, arch, llm, repair_engine, provider, model),
        _generate_one_schema("db_schema",   DB_SYSTEM,   intent, arch, llm, repair_engine, provider, model),
        _generate_one_schema("auth_schema", AUTH_SYSTEM, intent, arch, llm, repair_engine, provider, model),
        return_exceptions=False,
    )

    (ui_data, ui_metrics), (api_data, api_metrics), \
    (db_data, db_metrics), (auth_data, auth_metrics) = results

    ui   = UISchema.model_validate(apply_stage_auto_fixes("ui_schema", ui_data))
    try:
        api = APISchema.model_validate(apply_stage_auto_fixes("api_schema", api_data))
    except Exception:
        logger.warning("[Stage 3] API schema final validation failed — programmatic fallback")
        api = APISchema.model_validate(
            apply_stage_auto_fixes(
                "api_schema",
                build_api_schema_from_arch(arch.model_dump(), intent.model_dump()),
            )
        )
    db   = DBSchema.model_validate(db_data)
    auth = AuthSchema.model_validate(auth_data)

    logger.info(
        f"[Stage 3] Done | pages={len(ui.pages)} | endpoints={len(api.endpoints)} "
        f"| tables={len(db.tables)} | roles={len(auth.roles)}"
    )

    return ui, api, db, auth, [ui_metrics, api_metrics, db_metrics, auth_metrics]
