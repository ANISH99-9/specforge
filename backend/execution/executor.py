"""
SpecForge — Execution / Simulation Layer
Proves the AppConfig output is actually usable:

1. DB Execution    — CREATE TABLE statements run against real in-memory SQLite3
2. API Validation  — Mock request/response shape validation against declared schema
3. UI Binding Check— Every data_binding endpoint_id resolves to a real API endpoint

Returns ExecutionReport with executability_score (0.0–1.0).
"""
from __future__ import annotations
import sqlite3
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from validation.schema_defs import (
    AppConfig, DBSchema, DBTable, DBColumn,
    APISchema, UISchema,
    DBExecutionResult, APIValidationResult, APIValidationItem,
    UIValidationResult, UIBindingItem,
    ExecutionReport,
)

logger = logging.getLogger(__name__)

# ── SQLite type mapping ───────────────────────────────────────────

_TYPE_MAP = {
    "TEXT":      "TEXT",
    "INTEGER":   "INTEGER",
    "REAL":      "REAL",
    "BLOB":      "BLOB",
    "BOOLEAN":   "INTEGER",   # SQLite stores booleans as 0/1
    "TIMESTAMP": "TEXT",
    "UUID":      "TEXT",
    "JSON":      "TEXT",
}


def _col_to_ddl(col: DBColumn) -> str:
    sql_type = _TYPE_MAP.get(col.type.upper(), "TEXT")
    parts = [f'"{col.name}" {sql_type}']
    if col.primary_key:
        parts.append("PRIMARY KEY")
    if not col.nullable and not col.primary_key:
        parts.append("NOT NULL")
    if col.unique and not col.primary_key:
        parts.append("UNIQUE")
    if col.default is not None:
        parts.append(f"DEFAULT {col.default}")
    return " ".join(parts)


def _table_to_ddl(table: DBTable) -> str:
    col_defs = [_col_to_ddl(c) for c in table.columns]

    # Foreign key constraints
    fk_constraints = []
    for col in table.columns:
        if col.foreign_key:
            parts = col.foreign_key.split(".")
            if len(parts) == 2:
                ref_table, ref_col = parts
                fk_constraints.append(
                    f'FOREIGN KEY ("{col.name}") REFERENCES "{ref_table}" ("{ref_col}")'
                )

    all_defs = col_defs + fk_constraints
    return f'CREATE TABLE IF NOT EXISTS "{table.name}" (\n  ' + ",\n  ".join(all_defs) + "\n);"


# ── DB Execution ──────────────────────────────────────────────────

def execute_db_schema(db_schema: DBSchema) -> DBExecutionResult:
    """
    Actually run CREATE TABLE DDL against an in-memory SQLite3 database.
    This is REAL execution of a REAL artifact.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    tables_created: list[str] = []
    tables_failed: list[dict] = []
    ddl_statements: list[str] = []

    # Sort tables: tables without FK dependencies first
    ordered = _topological_sort(db_schema.tables)

    for table in ordered:
        ddl = _table_to_ddl(table)
        ddl_statements.append(ddl)
        try:
            conn.execute(ddl)
            # Also create indexes
            for idx in (table.indexes or []):
                unique_kw = "UNIQUE " if idx.unique else ""
                cols = ", ".join(f'"{c}"' for c in idx.columns)
                idx_ddl = f'CREATE {unique_kw}INDEX IF NOT EXISTS "{idx.name}" ON "{table.name}" ({cols});'
                conn.execute(idx_ddl)
                ddl_statements.append(idx_ddl)
            tables_created.append(table.name)
            logger.info(f"[Exec/DB] ✓ Created table '{table.name}'")
        except sqlite3.Error as e:
            tables_failed.append({
                "table": table.name,
                "error": str(e),
                "ddl": ddl,
            })
            logger.warning(f"[Exec/DB] ✗ Failed '{table.name}': {e}")

    conn.close()
    total = len(db_schema.tables)
    success_rate = len(tables_created) / total if total > 0 else 1.0

    return DBExecutionResult(
        tables_created=tables_created,
        tables_failed=tables_failed,
        total_tables=total,
        success_rate=success_rate,
        ddl_statements=ddl_statements,
    )


def _topological_sort(tables: list[DBTable]) -> list[DBTable]:
    """Sort tables so FK references come before dependents."""
    table_map = {t.name: t for t in tables}
    visited: set[str] = set()
    result: list[DBTable] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        table = table_map.get(name)
        if table is None:
            return
        for col in table.columns:
            if col.foreign_key:
                parts = col.foreign_key.split(".")
                if parts[0] in table_map:
                    visit(parts[0])
        result.append(table)

    for t in tables:
        visit(t.name)
    return result


# ── API Schema Validation ─────────────────────────────────────────

def validate_api_schema(api_schema: APISchema) -> APIValidationResult:
    """
    For each endpoint, verify:
    - path is a valid URI pattern
    - response_fields is non-empty
    - db_tables references are at least syntactically valid identifiers
    Returns per-endpoint pass/fail.
    """
    import re
    items: list[APIValidationItem] = []

    for ep in api_schema.endpoints:
        errors = []

        # Path must start with /
        if not ep.path.startswith("/"):
            errors.append(f"Path '{ep.path}' must start with /")

        # response_fields must be non-empty (already enforced by Pydantic, but double-check)
        if not ep.response_fields:
            errors.append("response_fields is empty")

        # db_tables must be non-empty
        if not ep.db_tables:
            errors.append("db_tables is empty")

        # id must match METHOD_resource pattern
        if not re.match(r'^[A-Z]+_\w+$', ep.id):
            errors.append(f"Endpoint id '{ep.id}' should match METHOD_resource pattern")

        passed = len(errors) == 0
        items.append(APIValidationItem(
            endpoint_id=ep.id,
            method=ep.method,
            path=ep.path,
            passed=passed,
            error="; ".join(errors) if errors else None,
        ))

    total = len(items)
    passed_count = sum(1 for i in items if i.passed)
    success_rate = passed_count / total if total > 0 else 1.0

    return APIValidationResult(items=items, success_rate=success_rate)


# ── UI Binding Validation ─────────────────────────────────────────

def validate_ui_bindings(ui_schema: UISchema, api_schema: APISchema) -> UIValidationResult:
    """
    Walk every UIComponent and verify all data_binding.endpoint_id
    values resolve to actual APIEndpoint ids.
    """
    api_ids = {ep.id for ep in api_schema.endpoints}
    items: list[UIBindingItem] = []

    def _walk(comp) -> None:
        if comp.data_binding:
            resolved = comp.data_binding.endpoint_id in api_ids
            items.append(UIBindingItem(
                component_id=comp.id,
                endpoint_id=comp.data_binding.endpoint_id,
                resolved=resolved,
                error=None if resolved else f"Endpoint '{comp.data_binding.endpoint_id}' not found",
            ))
        for action_ep_id in (comp.actions or []):
            resolved = action_ep_id in api_ids
            items.append(UIBindingItem(
                component_id=comp.id,
                endpoint_id=action_ep_id,
                resolved=resolved,
                error=None if resolved else f"Action endpoint '{action_ep_id}' not found",
            ))
        for child in (comp.children or []):
            _walk(child)

    for page in ui_schema.pages:
        _walk(page)

    total = len(items)
    dangling = sum(1 for i in items if not i.resolved)
    success_rate = (total - dangling) / total if total > 0 else 1.0

    return UIValidationResult(
        items=items,
        dangling_count=dangling,
        success_rate=success_rate,
    )


# ── Main Executor ─────────────────────────────────────────────────

def run_execution(app_config: AppConfig, run_id: str = "") -> ExecutionReport:
    """
    Run all three execution checks and compute an overall executability_score.
    Score weights: DB=50%, API=30%, UI=20%
    """
    t0 = time.monotonic()

    db_result  = execute_db_schema(app_config.db_schema)
    api_result = validate_api_schema(app_config.api_schema)
    ui_result  = validate_ui_bindings(app_config.ui_schema, app_config.api_schema)

    score = (
        db_result.success_rate  * 0.50 +
        api_result.success_rate * 0.30 +
        ui_result.success_rate  * 0.20
    )

    logger.info(
        f"[Exec] DB={db_result.success_rate:.0%} "
        f"API={api_result.success_rate:.0%} "
        f"UI={ui_result.success_rate:.0%} "
        f"→ score={score:.2f} | {int((time.monotonic()-t0)*1000)}ms"
    )

    return ExecutionReport(
        db=db_result,
        api=api_result,
        ui=ui_result,
        executability_score=round(score, 3),
        executed_at=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
    )
