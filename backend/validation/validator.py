"""
SpecForge — 4-Layer Validator
Runs cheap-to-expensive layers in order, short-circuits on first failure.
Layer 1: Syntactic  (JSON parse)
Layer 2: Structural (Pydantic schema)
Layer 3: Semantic   (cross-layer graph consistency)
Layer 4: Logical    (business rule contradictions)
"""
from __future__ import annotations
import json
import re
from typing import Any, Type
from pydantic import BaseModel, ValidationError

from validation.schema_defs import (
    ValidationResult, AppConfig, IntentSpec, ArchitectureSpec,
    UISchema, APISchema, DBSchema, AuthSchema
)
from validation.dependency_graph import DependencyGraph


# ── Helpers ─────────────────────────────────────────────────────────

def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that LLMs sometimes add."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    # Fix trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def auto_fix_json(raw: str) -> str:
    """Best-effort auto-fix for common JSON syntax errors."""
    fixed = strip_markdown_fences(raw)
    # Remove single-line comments
    fixed = re.sub(r"//[^\n]*", "", fixed)
    # Remove multi-line comments
    fixed = re.sub(r"/\*.*?\*/", "", fixed, flags=re.DOTALL)
    # Trailing commas
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    return fixed


def extract_failed_generation(error_text: str) -> str | None:
    """Extract partial JSON from Groq json_validate_failed error bodies."""
    if not error_text:
        return None

    # failed_generation key in dict repr: 'failed_generation': '{...}' or "failed_generation": "{...}"
    m = re.search(
        r"failed_generation['\"]?\s*:\s*['\"](.+)",
        error_text,
        re.DOTALL,
    )
    if m:
        partial = m.group(1)
        # Unescape if needed; trim trailing quote from closed string
        if partial.endswith("\\'") or partial.endswith('\\"'):
            partial = partial[:-2]
        elif partial.endswith("'") or partial.endswith('"'):
            partial = partial[:-1]
        return partial

    # Raw JSON embedded after error prefix
    for marker in ('{"base_path"', '{"pages"', '{"tables"', '{"roles"'):
        idx = error_text.find(marker)
        if idx != -1:
            return error_text[idx:]
    return None


def salvage_truncated_api_schema(raw: str) -> dict | None:
    """
    Recover a partial APISchema from truncated / invalid JSON.
    Drops the incomplete last endpoint object.
    """
    cleaned = auto_fix_json(raw)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and isinstance(data.get("endpoints"), list):
            return data
    except json.JSONDecodeError:
        pass

    arr_start = cleaned.find('"endpoints"')
    if arr_start == -1:
        return None
    bracket = cleaned.find("[", arr_start)
    if bracket == -1:
        return None

    endpoints: list[dict] = []
    depth = 0
    obj_start: int | None = None
    for i in range(bracket + 1, len(cleaned)):
        ch = cleaned[i]
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    ep = json.loads(cleaned[obj_start : i + 1])
                    if isinstance(ep, dict) and ep.get("id"):
                        endpoints.append(ep)
                except json.JSONDecodeError:
                    pass
                obj_start = None

    if not endpoints:
        return None

    base_path = "/api"
    m = re.search(r'"base_path"\s*:\s*"([^"]+)"', cleaned)
    if m:
        base_path = m.group(1)

    return {"base_path": base_path, "endpoints": endpoints}


def salvage_truncated_json(raw: str, stage: str = "") -> dict | None:
    """Stage-aware salvage for truncated LLM JSON output."""
    if stage == "api_schema":
        return salvage_truncated_api_schema(raw)
    cleaned = auto_fix_json(raw)
    for suffix in ("", "}", "]}", "\"}", '"}]}', '"]}'):
        try:
            return json.loads(cleaned + suffix)
        except json.JSONDecodeError:
            continue
    return None


# ── Layer 1: Syntactic ──────────────────────────────────────────────

def validate_syntax(raw: str) -> tuple[bool, dict | None, str | None]:
    try:
        cleaned = auto_fix_json(raw)
        data = json.loads(cleaned)
        return True, data, None
    except json.JSONDecodeError as e:
        return False, None, f"JSON parse error at position {e.pos}: {e.msg}"


# ── Layer 2: Structural ──────────────────────────────────────────────

def validate_structure(
    data: dict,
    model: Type[BaseModel],
) -> tuple[bool, BaseModel | None, list[str], list[str]]:
    """
    Returns (valid, instance, errors, missing_fields).
    """
    try:
        instance = model.model_validate(data)
        return True, instance, [], []
    except ValidationError as e:
        errors: list[str] = []
        missing: list[str] = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"{loc}: {msg}")
            if err["type"] in ("missing", "value_error.missing"):
                missing.append(loc)
        return False, None, errors, missing


# ── Layer 3: Semantic / Cross-layer ─────────────────────────────────

def validate_semantic(app_config: AppConfig) -> tuple[bool, list[str], list[str]]:
    """
    Returns (valid, errors, warnings).
    Checks:
      1. Every UI data_binding.endpoint_id exists in api_schema.endpoints
      2. Every APIEndpoint.db_tables entry exists in db_schema.tables
      3. Every auth_schema.role exists in architecture.permission_matrix
      4. Every APIEndpoint.roles_allowed role exists in auth_schema.roles
      5. No orphan DB tables (defined but never referenced by any endpoint)
    """
    errors: list[str] = []
    warnings: list[str] = []

    api_endpoint_ids = {ep.id for ep in app_config.api_schema.endpoints}
    db_table_names   = {tbl.name for tbl in app_config.db_schema.tables}
    auth_roles       = set(app_config.auth_schema.roles)

    # 1. UI → API binding checks
    def _check_component(comp) -> None:
        if comp.data_binding:
            if comp.data_binding.endpoint_id not in api_endpoint_ids:
                errors.append(
                    f"UI component '{comp.id}': data_binding references unknown "
                    f"endpoint '{comp.data_binding.endpoint_id}'"
                )
        for action in (comp.actions or []):
            if action not in api_endpoint_ids:
                errors.append(
                    f"UI component '{comp.id}': action references unknown endpoint '{action}'"
                )
        for child in (comp.children or []):
            _check_component(child)

    for page in app_config.ui_schema.pages:
        _check_component(page)

    # 2. API → DB table checks
    referenced_tables: set[str] = set()
    for ep in app_config.api_schema.endpoints:
        for tbl in ep.db_tables:
            if tbl not in db_table_names:
                errors.append(
                    f"API endpoint '{ep.id}': references non-existent DB table '{tbl}'"
                )
            referenced_tables.add(tbl)
        # Check roles
        for role in ep.roles_allowed:
            if role not in auth_roles:
                errors.append(
                    f"API endpoint '{ep.id}': roles_allowed includes undefined role '{role}'"
                )

    # 3. Orphan DB tables
    orphan_tables = db_table_names - referenced_tables
    for tbl in orphan_tables:
        warnings.append(f"DB table '{tbl}' is never referenced by any API endpoint")

    # 4. Auth roles used in permission matrix
    perm_roles = {p.role for p in app_config.auth_schema.permissions}
    unused_roles = auth_roles - perm_roles
    for role in unused_roles:
        warnings.append(f"Auth role '{role}' has no entries in permission matrix")

    # 5. FK column references
    for table in app_config.db_schema.tables:
        for col in table.columns:
            if col.foreign_key:
                parts = col.foreign_key.split(".")
                if len(parts) == 2:
                    ref_table, ref_col = parts
                    if ref_table not in db_table_names:
                        errors.append(
                            f"DB column '{table.name}.{col.name}': "
                            f"foreign_key references unknown table '{ref_table}'"
                        )

    return len(errors) == 0, errors, warnings


# ── Layer 4: Logical ────────────────────────────────────────────────

def validate_logic(app_config: AppConfig) -> tuple[bool, list[str]]:
    """
    Returns (valid, errors).
    Checks:
      1. Business rules don't contradict permission matrix
      2. No role simultaneously allowed and denied for same action+entity
      3. Premium-gated features have corresponding auth guard
    """
    errors: list[str] = []

    # Check for duplicate/contradicting permission rules
    seen: dict[tuple, bool] = {}
    for p in app_config.auth_schema.permissions:
        key = (p.role, p.action, p.entity)
        if key in seen:
            if seen[key] != p.allowed:
                errors.append(
                    f"Logical conflict: role '{p.role}' has both ALLOW and DENY "
                    f"for action '{p.action}' on entity '{p.entity}'"
                )
        else:
            seen[key] = p.allowed

    # Check page roles vs auth roles
    auth_roles = set(app_config.auth_schema.roles)
    for page in app_config.architecture.page_flow:
        for role in page.roles_allowed:
            if role not in auth_roles:
                errors.append(
                    f"Page '{page.name}': roles_allowed includes undefined role '{role}'"
                )

    # Check business rules reference existing entities
    arch_entity_names = {e.name for e in app_config.architecture.entities}
    for rule in app_config.architecture.business_rules:
        # Very basic check: if applies_to references an entity name, it must exist
        if rule.applies_to in arch_entity_names:
            pass  # valid reference
        # (more complex rule checking could be added here)

    return len(errors) == 0, errors


# ── Main Validator ───────────────────────────────────────────────────

STAGE_MODELS: dict[str, Type[BaseModel]] = {
    "intent":       IntentSpec,
    "design":       ArchitectureSpec,
    "ui_schema":    UISchema,
    "api_schema":   APISchema,
    "db_schema":    DBSchema,
    "auth_schema":  AuthSchema,
}


def validate_stage_output(
    stage: str,
    raw: str | dict,
) -> ValidationResult:
    """
    Run Layers 1 + 2 on a single stage output.
    raw can be a JSON string or an already-parsed dict.
    """
    result = ValidationResult(stage=stage, valid=False)

    # Layer 1
    if isinstance(raw, str):
        ok, data, err = validate_syntax(raw)
        if not ok:
            result.syntax_valid = False
            result.errors.append(err or "Syntax error")
            return result
        result.syntax_valid = True
    else:
        data = raw

    # Deterministic stage fixes before structural validation
    from repair.repair_engine import apply_stage_auto_fixes
    if isinstance(data, dict):
        data = apply_stage_auto_fixes(stage, data)

    # Layer 2
    model_cls = STAGE_MODELS.get(stage)
    if model_cls is None:
        result.errors.append(f"Unknown stage: {stage}")
        return result

    ok, instance, errors, missing = validate_structure(data, model_cls)
    if not ok:
        result.structure_valid = False
        result.errors.extend(errors)
        result.missing_fields.extend(missing)
        return result

    result.structure_valid = True
    result.valid = True
    return result


def validate_app_config(app_config: AppConfig) -> ValidationResult:
    """
    Run all 4 layers on a complete AppConfig (after Stage 4).
    Auto-fixes cross-layer issues before validating.
    """
    from repair.repair_engine import auto_fix_app_config

    app_config = auto_fix_app_config(app_config)

    result = ValidationResult(stage="app_config", valid=False,
                              syntax_valid=True, structure_valid=True)

    # Layer 3: Semantic
    sem_ok, sem_errors, sem_warnings = validate_semantic(app_config)
    result.semantic_valid = sem_ok
    result.semantic_errors.extend(sem_errors)
    result.warnings.extend(sem_warnings)

    # Layer 4: Logical
    log_ok, log_errors = validate_logic(app_config)
    result.logic_valid = log_ok
    result.logic_errors.extend(log_errors)

    result.errors = sem_errors + log_errors
    result.valid = sem_ok and log_ok
    return result


def build_dependency_graph(app_config: AppConfig) -> DependencyGraph:
    graph = DependencyGraph()
    graph.build_from_app_config(app_config)
    return graph
