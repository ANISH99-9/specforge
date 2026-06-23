"""
SpecForge — Repair Engine
Graph-based localization + targeted patch strategy.
This is the highest-value component — NOT blind full-retry.

Failure classification → Localization → Strategy → Targeted repair → Re-validate → Cap
"""
from __future__ import annotations
import json
import re
import asyncio
import logging
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel

from validation.schema_defs import ValidationResult
from validation.validator import (
    validate_stage_output, auto_fix_json,
    salvage_truncated_json, salvage_truncated_api_schema,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────

class FailureType(str, Enum):
    SYNTAX              = "syntax"
    MISSING_FIELD       = "missing_field"
    HALLUCINATED_FIELD  = "hallucinated_field"
    CROSS_LAYER_MISMATCH = "cross_layer_mismatch"
    LOGICAL_CONFLICT    = "logical_conflict"


class RepairStrategy(str, Enum):
    AUTO_FIX            = "auto_fix"           # strip fences, fix commas, re-parse
    TARGETED_REPROMPT   = "targeted_reprompt"  # ask LLM to regenerate only missing fields
    STRIP_AND_LOG       = "strip_and_log"      # remove bad field, log, keep rest
    REGEN_DOWNSTREAM    = "regen_downstream"   # re-call the downstream stage only
    REGEN_BUSINESS_RULE = "regen_business_rule"  # re-generate the conflicting rule


class RepairAttempt(BaseModel):
    iteration: int
    failure_type: str
    strategy_used: str
    success: bool
    errors_before: list[str]
    errors_after: list[str]


class RepairResult(BaseModel):
    success: bool
    iterations: int
    failure_type: str
    strategy_used: str
    patched_data: Optional[dict] = None
    escalated: bool = False
    error_message: Optional[str] = None
    attempts: list[RepairAttempt] = []


# ─────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────

def classify_failure(validation_result: ValidationResult) -> FailureType:
    """Determine the root failure type from a ValidationResult."""
    if not validation_result.syntax_valid:
        return FailureType.SYNTAX
    if validation_result.missing_fields:
        return FailureType.MISSING_FIELD
    if validation_result.semantic_errors:
        return FailureType.CROSS_LAYER_MISMATCH
    if validation_result.logic_errors:
        return FailureType.LOGICAL_CONFLICT
    # Structural errors that aren't just missing fields = possible hallucinated types
    if not validation_result.structure_valid:
        return FailureType.HALLUCINATED_FIELD
    return FailureType.MISSING_FIELD


def choose_strategy(failure_type: FailureType) -> RepairStrategy:
    return {
        FailureType.SYNTAX:               RepairStrategy.AUTO_FIX,
        FailureType.MISSING_FIELD:        RepairStrategy.TARGETED_REPROMPT,
        FailureType.HALLUCINATED_FIELD:   RepairStrategy.STRIP_AND_LOG,
        FailureType.CROSS_LAYER_MISMATCH: RepairStrategy.REGEN_DOWNSTREAM,
        FailureType.LOGICAL_CONFLICT:     RepairStrategy.REGEN_BUSINESS_RULE,
    }[failure_type]


# ─────────────────────────────────────────────
# Auto-fix strategies (no LLM needed)
# ─────────────────────────────────────────────

def _auto_fix_syntax(raw: str) -> Optional[dict]:
    """Strip fences, fix commas, attempt parse. Returns dict or None."""
    try:
        return json.loads(auto_fix_json(raw))
    except Exception:
        return None


def _auto_fix_api_schema(data: dict) -> dict:
    """Ensure every endpoint has at least one response_field."""
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list):
        return data

    for endpoint in endpoints:
        if isinstance(endpoint, dict) and not endpoint.get("response_fields"):
            endpoint["response_fields"] = [
                {"name": "success", "type": "boolean", "required": True},
            ]
            logger.info(
                f"[Repair] Auto-filled empty response_fields for endpoint "
                f"{endpoint.get('id', '?')}"
            )
    return data


_ALLOWED_UI_TYPES = {
    "page", "section", "card", "table", "form", "button",
    "chart", "badge", "nav", "sidebar", "modal", "input", "select",
}

# Common LLM-hallucinated types → allowed type
_UI_TYPE_REMAP: dict[str, str] = {
    "list_item": "card",
    "list": "section",
    "text": "card",
    "metric_display": "badge",
    "column": "input",
    "field": "input",
    "row": "section",
    "header": "section",
    "footer": "section",
    "link": "button",
    "dropdown": "select",
}


def _resolve_ui_component_type(node: dict, parent_type: str | None) -> str:
    """
    Map invalid UI types to allowed ones.
    table_column: input inside table/form (option 2), card elsewhere (option 1).
    """
    comp_type = str(node.get("type", ""))
    if comp_type in _ALLOWED_UI_TYPES:
        return comp_type

    if comp_type == "table_column":
        if parent_type in ("table", "form"):
            return "input"
        return "card"

    return _UI_TYPE_REMAP.get(comp_type, "card")


def _auto_fix_ui_component(node: dict, parent_type: str | None = None) -> dict:
    """Recursively repair invalid UI component types."""
    if not isinstance(node, dict):
        return node

    old_type = node.get("type")
    new_type = _resolve_ui_component_type(node, parent_type)
    if old_type and old_type != new_type:
        node["type"] = new_type
        logger.info(
            f"[Repair] UI component '{node.get('id', '?')}': "
            f"{old_type} → {new_type}"
        )

    children = node.get("children")
    if isinstance(children, list):
        node["children"] = [
            _auto_fix_ui_component(child, node.get("type"))
            for child in children
            if isinstance(child, dict)
        ]

    return node


def _auto_fix_ui_schema(data: dict) -> dict:
    """Repair hallucinated UI component types (e.g. table_column → input/card)."""
    pages = data.get("pages")
    if not isinstance(pages, list):
        return data

    data["pages"] = [
        _auto_fix_ui_component(page, None)
        for page in pages
        if isinstance(page, dict)
    ]
    return data


def apply_stage_auto_fixes(stage: str, data: dict) -> dict:
    """Deterministic stage-specific repairs (no LLM)."""
    if not isinstance(data, dict):
        return data
    if stage == "api_schema":
        return _auto_fix_api_schema(data)
    if stage == "ui_schema":
        return _auto_fix_ui_schema(data)
    return data


def _map_arch_field_type(field_type: str) -> str:
    mapping = {
        "text": "string",
        "relation": "uuid",
        "datetime": "datetime",
        "enum": "enum",
        "number": "number",
        "boolean": "boolean",
        "uuid": "uuid",
        "string": "string",
    }
    return mapping.get(field_type, "string")


def _default_endpoint(
    eid: str,
    method: str,
    path: str,
    description: str,
    roles: list[str],
    table: str,
    *,
    request_body: list[dict] | None = None,
    path_params: list[dict] | None = None,
    response_fields: list[dict] | None = None,
) -> dict:
    return {
        "id": eid,
        "method": method,
        "path": path,
        "description": description,
        "request_body": request_body,
        "path_params": path_params,
        "response_fields": response_fields or [
            {"name": "id", "type": "uuid", "required": True},
            {"name": "success", "type": "boolean", "required": True},
        ],
        "auth_required": eid not in ("POST_auth_login", "POST_auth_register"),
        "roles_allowed": roles,
        "db_tables": [table],
    }


def build_api_schema_from_arch(arch: dict, intent: dict | None = None) -> dict:
    """
    Programmatic APISchema fallback when LLM output is truncated or invalid.
    Generates auth + CRUD endpoints for every architecture entity.
    """
    roles = (intent or {}).get("roles") or ["Admin", "User"]
    endpoints: list[dict] = [
        _default_endpoint(
            "POST_auth_login", "POST", "/api/auth/login",
            "Authenticate user and return JWT", roles, "users",
            request_body=[
                {"name": "email", "type": "string", "required": True},
                {"name": "password", "type": "string", "required": True},
            ],
            response_fields=[
                {"name": "token", "type": "string", "required": True},
                {"name": "user", "type": "object", "required": True},
            ],
        ),
        _default_endpoint(
            "POST_auth_register", "POST", "/api/auth/register",
            "Register a new user", roles, "users",
            request_body=[
                {"name": "email", "type": "string", "required": True},
                {"name": "password", "type": "string", "required": True},
                {"name": "name", "type": "string", "required": True},
            ],
            response_fields=[
                {"name": "token", "type": "string", "required": True},
                {"name": "user", "type": "object", "required": True},
            ],
        ),
    ]

    for entity in arch.get("entities") or []:
        name = str(entity.get("name", "Item"))
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        plural = slug + "s" if not slug.endswith("s") else slug
        table = plural
        fields = entity.get("fields") or []
        resp_fields = [
            {
                "name": f.get("name", "id"),
                "type": _map_arch_field_type(str(f.get("type", "string"))),
                "required": bool(f.get("required", True)),
            }
            for f in fields[:5]
        ]
        if not resp_fields:
            resp_fields = [{"name": "id", "type": "uuid", "required": True}]

        endpoints.extend([
            _default_endpoint(
                f"GET_{plural}", "GET", f"/api/{plural}",
                f"List all {name} records", roles, table,
                response_fields=resp_fields,
            ),
            _default_endpoint(
                f"GET_{plural}_by_id", "GET", f"/api/{plural}/{{id}}",
                f"Get a single {name} by id", roles, table,
                path_params=[{"name": "id", "type": "uuid", "required": True}],
                response_fields=resp_fields,
            ),
            _default_endpoint(
                f"POST_{plural}", "POST", f"/api/{plural}",
                f"Create a new {name}", roles, table,
                request_body=resp_fields[:4],
                response_fields=resp_fields[:3],
            ),
            _default_endpoint(
                f"PUT_{plural}", "PUT", f"/api/{plural}/{{id}}",
                f"Update an existing {name}", roles, table,
                path_params=[{"name": "id", "type": "uuid", "required": True}],
                request_body=resp_fields[:4],
                response_fields=resp_fields[:3],
            ),
        ])

    return {"base_path": "/api", "endpoints": endpoints}


def _merge_api_schemas(partial: dict, fallback: dict) -> dict:
    """Keep salvaged endpoints, fill gaps from programmatic fallback."""
    partial_eps = {
        ep["id"]: ep
        for ep in (partial.get("endpoints") or [])
        if isinstance(ep, dict) and ep.get("id")
    }
    fallback_eps = {
        ep["id"]: ep
        for ep in (fallback.get("endpoints") or [])
        if isinstance(ep, dict) and ep.get("id")
    }
    merged_ids = list(dict.fromkeys(list(partial_eps.keys()) + list(fallback_eps.keys())))
    merged = [partial_eps.get(eid) or fallback_eps[eid] for eid in merged_ids]
    return {
        "base_path": partial.get("base_path") or fallback.get("base_path") or "/api",
        "endpoints": merged,
    }


def _default_db_table(table_name: str) -> dict:
    """Create a minimal valid DB table for a missing referenced table."""
    if table_name == "users":
        return {
            "name": "users",
            "columns": [
                {"name": "id", "type": "UUID", "primary_key": True, "nullable": False},
                {"name": "email", "type": "TEXT", "nullable": False, "unique": True},
                {"name": "password_hash", "type": "TEXT", "nullable": False},
                {"name": "role", "type": "TEXT", "nullable": False, "default": "user"},
                {"name": "created_at", "type": "TIMESTAMP", "nullable": False, "default": "CURRENT_TIMESTAMP"},
                {"name": "updated_at", "type": "TIMESTAMP", "nullable": False, "default": "CURRENT_TIMESTAMP"},
            ],
            "indexes": [
                {"name": "idx_users_email", "columns": ["email"], "unique": True},
            ],
        }

    return {
        "name": table_name,
        "columns": [
            {"name": "id", "type": "UUID", "primary_key": True, "nullable": False},
            {"name": "name", "type": "TEXT", "nullable": False},
            {"name": "created_at", "type": "TIMESTAMP", "nullable": False, "default": "CURRENT_TIMESTAMP"},
            {"name": "updated_at", "type": "TIMESTAMP", "nullable": False, "default": "CURRENT_TIMESTAMP"},
        ],
        "indexes": [
            {"name": f"idx_{table_name}_created", "columns": ["created_at"], "unique": False},
        ],
    }


def auto_fix_app_config(app_config):
    """
    Deterministic cross-layer repair for AppConfig (no LLM).
    Fixes:
      - API endpoints referencing missing DB tables → add tables
      - Pages/API using undefined roles → add roles + default permissions
    """
    from validation.schema_defs import (
        AppConfig, DBSchema, AuthSchema, DBTable, PermissionRule, Conflict,
    )

    if not isinstance(app_config, AppConfig):
        return app_config

    # ── 1. Collect every role referenced across all layers ──
    referenced_roles: set[str] = set(app_config.intent.roles)
    referenced_roles.update(app_config.auth_schema.roles)
    for page in app_config.architecture.page_flow:
        referenced_roles.update(page.roles_allowed)
    for ep in app_config.api_schema.endpoints:
        referenced_roles.update(ep.roles_allowed)

    def _collect_ui_roles(comp) -> None:
        if comp.roles_visible:
            referenced_roles.update(comp.roles_visible)
        for child in (comp.children or []):
            _collect_ui_roles(child)

    for page in app_config.ui_schema.pages:
        _collect_ui_roles(page)

    existing_roles = set(app_config.auth_schema.roles)
    missing_roles = referenced_roles - existing_roles

    if missing_roles:
        new_roles = list(existing_roles | missing_roles)
        new_permissions = list(app_config.auth_schema.permissions)
        entities = [e.name for e in app_config.architecture.entities] or ["Resource"]

        for role in missing_roles:
            logger.info(f"[Repair] Adding missing auth role: {role}")
            for entity in entities:
                for action in ("create", "read", "update", "delete", "list"):
                    new_permissions.append(PermissionRule(
                        role=role,
                        action=action,
                        entity=entity,
                        allowed=True,
                    ))

        app_config.auth_schema = AuthSchema(
            roles=new_roles,
            permissions=new_permissions,
            route_guards=app_config.auth_schema.route_guards,
            session_type=app_config.auth_schema.session_type,
            providers=app_config.auth_schema.providers,
        )
        app_config.conflicts.append(Conflict(
            description=f"Added undefined roles to auth_schema: {', '.join(sorted(missing_roles))}",
            resolution="Auto-added roles with default full permissions on all entities",
            source="cross_layer_auto_fix",
            severity="warning",
        ))

    # ── 2. Add missing DB tables referenced by API endpoints ──
    db_table_names = {t.name for t in app_config.db_schema.tables}
    referenced_tables: set[str] = set()
    for ep in app_config.api_schema.endpoints:
        referenced_tables.update(ep.db_tables)

    missing_tables = referenced_tables - db_table_names
    if missing_tables:
        tables = list(app_config.db_schema.tables)
        for tbl_name in sorted(missing_tables):
            logger.info(f"[Repair] Adding missing DB table: {tbl_name}")
            tables.append(DBTable.model_validate(_default_db_table(tbl_name)))
            app_config.conflicts.append(Conflict(
                description=f"API referenced missing DB table '{tbl_name}'",
                resolution=f"Auto-created '{tbl_name}' table with standard columns",
                source="cross_layer_auto_fix",
                severity="warning",
            ))
        app_config.db_schema = DBSchema(tables=tables)

    return app_config


def _parse_raw_to_dict(raw_output: str | dict, stage: str) -> dict | None:
    """Parse, salvage, or return dict from raw LLM output."""
    if isinstance(raw_output, dict):
        return raw_output
    try:
        return json.loads(auto_fix_json(str(raw_output)))
    except json.JSONDecodeError:
        pass
    salvaged = salvage_truncated_json(str(raw_output), stage)
    if salvaged:
        return salvaged
    if stage == "api_schema":
        return salvage_truncated_api_schema(str(raw_output))
    return None


def _strip_hallucinated_fields(data: dict, model_fields: set[str]) -> dict:
    """Remove keys not in the Pydantic model's field set."""
    if not isinstance(data, dict):
        return data
    return {k: v for k, v in data.items() if k in model_fields}


def _get_model_fields(stage: str) -> set[str]:
    from validation.schema_defs import (
        IntentSpec, ArchitectureSpec, UISchema, APISchema, DBSchema, AuthSchema
    )
    models = {
        "intent":      IntentSpec,
        "design":      ArchitectureSpec,
        "ui_schema":   UISchema,
        "api_schema":  APISchema,
        "db_schema":   DBSchema,
        "auth_schema": AuthSchema,
    }
    m = models.get(stage)
    return set(m.model_fields.keys()) if m else set()


# ─────────────────────────────────────────────
# LLM-based repair prompts
# ─────────────────────────────────────────────

TARGETED_REPROMPT_TEMPLATE = """You are fixing a JSON output that failed validation.

Stage: {stage}
Missing fields: {missing_fields}
Validation errors: {errors}

Current (incomplete) JSON:
{current_json}

Instructions:
1. Add ONLY the missing fields listed above
2. Do NOT change any existing valid fields
3. Return the COMPLETE corrected JSON (all fields, not just the additions)
4. Return ONLY valid JSON, no markdown, no explanation
5. Temperature is 0 — be precise and deterministic

Corrected JSON:"""

ENDPOINT_REPAIR_TEMPLATE = """You are fixing ONE API endpoint JSON object that failed validation.

Endpoint validation errors:
{errors}

Endpoint JSON (broken):
{endpoint_json}

Rules:
1. Return ONLY a single JSON object representing the corrected endpoint (not the whole APISchema)
2. Do NOT remove required fields: id, method, path, description, response_fields, auth_required, roles_allowed, db_tables
3. response_fields MUST be a non-empty array (min 1). Keep it concise (<= 5 fields).
4. Field types MUST be one of: string, number, boolean, object, array, uuid, datetime, enum
5. Return ONLY valid JSON, no markdown, no explanation. Temperature 0.

Corrected endpoint JSON:"""

REGEN_DOWNSTREAM_TEMPLATE = """You are fixing a cross-layer consistency error in a JSON schema.

Stage to fix: {stage}
Cross-layer errors:
{semantic_errors}

Source of truth (do NOT change this):
{source_of_truth}

Current (broken) output:
{current_json}

Instructions:
1. Fix the {stage} to be consistent with the source of truth
2. Resolve EVERY error listed above
3. Return ONLY valid JSON, no markdown
4. Return the COMPLETE corrected JSON

Corrected JSON:"""

REGEN_BUSINESS_RULE_TEMPLATE = """You are resolving a logical conflict in business rules or permissions.

Stage: {stage}
Logical conflicts:
{logic_errors}

Current JSON (with conflict):
{current_json}

Instructions:
1. Resolve each conflict by picking the more permissive/reasonable option
2. Add a comment field "conflict_resolution_note" explaining what you chose (this is EXTRA, not in schema — I'll strip it)
3. Return ONLY valid JSON, no markdown

Corrected JSON:"""


# ─────────────────────────────────────────────
# Repair Engine
# ─────────────────────────────────────────────

class RepairEngine:
    def __init__(self, llm_client, max_retries: int = 3):
        self.llm = llm_client
        self.max_retries = max_retries

    async def repair(
        self,
        raw_output: str | dict,
        validation_result: ValidationResult,
        stage: str,
        context: dict | None = None,
    ) -> RepairResult:
        """
        Main repair loop:
        classify → localize → strategy → patch → re-validate → cap
        """
        current = raw_output if isinstance(raw_output, str) else json.dumps(raw_output)
        current_data: Optional[dict] = None
        attempts: list[RepairAttempt] = []
        context = context or {}

        # Parse or salvage truncated JSON (common with long app descriptions)
        parsed = _parse_raw_to_dict(raw_output, stage)
        if parsed is None and stage == "api_schema" and context.get("arch"):
            logger.warning("[Repair] Salvage failed — building API schema from architecture")
            parsed = build_api_schema_from_arch(context["arch"], context.get("intent"))
        elif parsed is not None and stage == "api_schema" and context.get("arch"):
            fallback = build_api_schema_from_arch(context["arch"], context.get("intent"))
            parsed = _merge_api_schemas(parsed, fallback)

        if isinstance(parsed, dict):
            parsed = apply_stage_auto_fixes(stage, parsed)
            current = json.dumps(parsed)
            pre_validation = validate_stage_output(stage, parsed)
            if pre_validation.valid:
                return RepairResult(
                    success=True,
                    iterations=0,
                    failure_type=FailureType.SYNTAX,
                    strategy_used=RepairStrategy.AUTO_FIX,
                    patched_data=parsed,
                    escalated=False,
                    attempts=[],
                )
            validation_result = pre_validation

        # Repair ALL failing API endpoints (surgical, one at a time)
        if stage == "api_schema" and isinstance(parsed, dict):
            parsed = await self._repair_all_api_endpoints(parsed, validation_result)
            if parsed:
                parsed = apply_stage_auto_fixes(stage, parsed)
                re_val = validate_stage_output(stage, parsed)
                if re_val.valid:
                    return RepairResult(
                        success=True,
                        iterations=1,
                        failure_type=FailureType.MISSING_FIELD,
                        strategy_used="endpoint_repair_all",
                        patched_data=parsed,
                        escalated=False,
                        attempts=[],
                    )
                validation_result = re_val
                current = json.dumps(parsed)

        for iteration in range(1, self.max_retries + 1):
            failure_type = classify_failure(validation_result)
            strategy     = choose_strategy(failure_type)

            logger.info(
                f"[Repair] stage={stage} iter={iteration} "
                f"failure={failure_type} strategy={strategy}"
            )

            errors_before = list(validation_result.errors)

            # Execute chosen strategy
            patched_str: Optional[str] = None
            patched_data: Optional[dict] = None

            if strategy == RepairStrategy.AUTO_FIX:
                patched_data = _auto_fix_syntax(current)
                if patched_data:
                    patched_str = json.dumps(patched_data)

            elif strategy == RepairStrategy.STRIP_AND_LOG:
                import json as _json
                try:
                    d = _json.loads(auto_fix_json(current))
                    model_fields = _get_model_fields(stage)
                    if model_fields:
                        d = _strip_hallucinated_fields(d, model_fields)
                    patched_data = d
                    patched_str = json.dumps(d)
                    logger.warning(
                        f"[Repair] Stripped hallucinated fields from {stage}. "
                        f"Kept: {list(d.keys())}"
                    )
                except Exception:
                    pass

            elif strategy == RepairStrategy.TARGETED_REPROMPT:
                prompt = TARGETED_REPROMPT_TEMPLATE.format(
                    stage=stage,
                    missing_fields=", ".join(validation_result.missing_fields),
                    errors="\n".join(validation_result.errors[:5]),
                    current_json=current[:3000],
                )
                patched_str = await self._call_llm_for_repair(prompt, stage)
                if patched_str:
                    try:
                        patched_data = json.loads(auto_fix_json(patched_str))
                    except Exception:
                        pass

            elif strategy == RepairStrategy.REGEN_DOWNSTREAM:
                source_of_truth = json.dumps(context.get("source_of_truth", {}), indent=2)[:2000]
                prompt = REGEN_DOWNSTREAM_TEMPLATE.format(
                    stage=stage,
                    semantic_errors="\n".join(validation_result.semantic_errors[:5]),
                    source_of_truth=source_of_truth,
                    current_json=current[:3000],
                )
                patched_str = await self._call_llm_for_repair(prompt, stage)
                if patched_str:
                    try:
                        patched_data = json.loads(auto_fix_json(patched_str))
                    except Exception:
                        pass

            elif strategy == RepairStrategy.REGEN_BUSINESS_RULE:
                prompt = REGEN_BUSINESS_RULE_TEMPLATE.format(
                    stage=stage,
                    logic_errors="\n".join(validation_result.logic_errors[:5]),
                    current_json=current[:3000],
                )
                patched_str = await self._call_llm_for_repair(prompt, stage)
                if patched_str:
                    try:
                        patched_data = json.loads(auto_fix_json(patched_str))
                        # Strip extra conflict_resolution_note if LLM added it
                        patched_data.pop("conflict_resolution_note", None)
                    except Exception:
                        pass

            # Re-validate
            if patched_data is not None:
                patched_data = apply_stage_auto_fixes(stage, patched_data)
                new_validation = validate_stage_output(stage, patched_data)
            else:
                new_validation = validation_result

            attempt = RepairAttempt(
                iteration=iteration,
                failure_type=failure_type,
                strategy_used=strategy,
                success=new_validation.valid,
                errors_before=errors_before,
                errors_after=new_validation.errors,
            )
            attempts.append(attempt)

            if new_validation.valid:
                return RepairResult(
                    success=True,
                    iterations=iteration,
                    failure_type=failure_type,
                    strategy_used=strategy,
                    patched_data=patched_data,
                    escalated=False,
                    attempts=attempts,
                )

            # Prepare for next iteration
            validation_result = new_validation
            if patched_str:
                current = patched_str

        # Exhausted retries — last resort programmatic fallback for API schema
        if stage == "api_schema" and context.get("arch"):
            fallback = apply_stage_auto_fixes(
                stage,
                build_api_schema_from_arch(context["arch"], context.get("intent")),
            )
            fb_val = validate_stage_output(stage, fallback)
            if fb_val.valid:
                logger.warning("[Repair] Using programmatic API schema fallback")
                return RepairResult(
                    success=True,
                    iterations=self.max_retries,
                    failure_type=classify_failure(validation_result),
                    strategy_used="programmatic_fallback",
                    patched_data=fallback,
                    escalated=False,
                    attempts=attempts,
                )

        # Exhausted retries — escalate
        return RepairResult(
            success=False,
            iterations=self.max_retries,
            failure_type=classify_failure(validation_result),
            strategy_used=choose_strategy(classify_failure(validation_result)),
            patched_data=patched_data,
            escalated=True,
            error_message=(
                f"Repair failed after {self.max_retries} iterations. "
                f"Remaining errors: {validation_result.errors[:3]}"
            ),
            attempts=attempts,
        )

    async def _repair_all_api_endpoints(
        self,
        api_data: dict,
        validation_result: ValidationResult,
    ) -> dict | None:
        """Repair every endpoint index mentioned in validation errors."""
        endpoints = api_data.get("endpoints")
        if not isinstance(endpoints, list):
            return api_data

        failing_indices: set[int] = set()
        for err in validation_result.errors or []:
            m = re.search(r"endpoints\.(\d+)\.", err)
            if m:
                failing_indices.add(int(m.group(1)))

        if not failing_indices:
            # Also repair endpoints missing required fields
            for i, ep in enumerate(endpoints):
                if not isinstance(ep, dict):
                    failing_indices.add(i)
                    continue
                if not ep.get("response_fields") or not ep.get("db_tables"):
                    failing_indices.add(i)

        for idx in sorted(failing_indices):
            if idx < 0 or idx >= len(endpoints):
                continue
            endpoint = endpoints[idx]
            if not isinstance(endpoint, dict):
                continue
            prompt = ENDPOINT_REPAIR_TEMPLATE.format(
                errors="\n".join(validation_result.errors[:8]),
                endpoint_json=json.dumps(endpoint, indent=2)[:2000],
            )
            patched = await self._call_llm_for_repair(prompt, "api_schema")
            if not patched:
                endpoints[idx] = apply_stage_auto_fixes(
                    "api_schema",
                    {"base_path": "/api", "endpoints": [endpoint]},
                )["endpoints"][0]
                continue
            try:
                fixed_ep = json.loads(auto_fix_json(patched))
                if isinstance(fixed_ep, dict):
                    endpoints[idx] = fixed_ep
            except json.JSONDecodeError:
                endpoints[idx] = apply_stage_auto_fixes(
                    "api_schema",
                    {"base_path": "/api", "endpoints": [endpoint]},
                )["endpoints"][0]

        api_data["endpoints"] = endpoints
        return api_data

    async def _call_llm_for_repair(self, prompt: str, stage: str) -> Optional[str]:
        """Minimal LLM call for repair — uses Gemini with 2000 token cap."""
        try:
            response = await self.llm.complete_raw(
                provider=self.llm.settings.repair_provider,
                model=self.llm.settings.repair_model,
                system="You are a JSON repair assistant. Return ONLY valid JSON.",
                user=prompt,
                temperature=0.0,
                max_tokens=2000,
            )
            return response
        except Exception as e:
            logger.error(f"[Repair] LLM call failed: {e}")
            return None
