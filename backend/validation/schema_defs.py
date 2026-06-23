"""
SpecForge — Schema Definitions
All Pydantic v2 models for every pipeline stage.
These are the single source of truth for the entire system.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Literal, Any
from enum import Enum


# ─────────────────────────────────────────────
# STAGE 1 — Intent Extraction
# ─────────────────────────────────────────────

class EntityHint(BaseModel):
    name: str = Field(..., description="Entity name, PascalCase, e.g. 'Contact'")
    fields_hint: List[str] = Field(..., description="Likely fields, e.g. ['name','email','phone']")


class MonetizationSpec(BaseModel):
    has_premium_plan: bool = False
    gating_hint: Optional[str] = None


APP_TYPES = Literal[
    "CRM", "Marketplace", "SaaS Dashboard", "E-commerce", "Booking Platform",
    "Blog/CMS", "Analytics Tool", "LMS", "Social Platform", "Project Management",
    "Inventory System", "HR Tool", "Healthcare", "FinTech", "Other"
]


class IntentSpec(BaseModel):
    app_name: str
    app_type: APP_TYPES
    entities: List[EntityHint] = Field(..., min_length=1)
    roles: List[str] = Field(..., min_length=1)
    features: List[str] = Field(default_factory=list)  # relaxed — allows repair to fill in
    monetization: MonetizationSpec
    ambiguities: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def ensure_features(self) -> "IntentSpec":
        if not self.features:
            self.features = [f"Manage {e.name}" for e in self.entities[:3]]
        return self


# ─────────────────────────────────────────────
# STAGE 2 — Architecture / System Design
# ─────────────────────────────────────────────

class RelationType(str, Enum):
    ONE_TO_ONE   = "1:1"
    ONE_TO_MANY  = "1:N"
    MANY_TO_MANY = "N:N"


RELATION_TYPE_MAP = {
    # Normalize common LLM hallucinations → valid values
    "one-to-one": "1:1", "one_to_one": "1:1", "1-1": "1:1",
    "one-to-many": "1:N", "one_to_many": "1:N", "1-n": "1:N", "1-N": "1:N",
    "many-to-many": "N:N", "many_to_many": "N:N", "n-n": "N:N", "N-N": "N:N",
    "many-to-one": "1:N", "many_to_one": "1:N",
}


class EntityField(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "datetime", "enum", "relation", "text", "uuid"]
    required: bool = True
    enum_values: Optional[List[str]] = None
    relation_to: Optional[str] = None        # target entity name
    relation_type: Optional[RelationType] = None

    @field_validator("relation_type", mode="before")
    @classmethod
    def coerce_relation_type(cls, v):
        if v is None:
            return v
        normalized = RELATION_TYPE_MAP.get(str(v).strip(), str(v).strip())
        return normalized


class ArchEntity(BaseModel):
    name: str
    fields: List[EntityField] = Field(..., min_length=1)


class PermissionRule(BaseModel):
    role: str
    action: Literal["create", "read", "update", "delete", "list"]
    entity: str
    allowed: bool = True
    condition: Optional[str] = None          # e.g. "user.id == resource.owner_id"


class Page(BaseModel):
    name: str
    path: str
    roles_allowed: List[str]
    parent: Optional[str] = None
    description: Optional[str] = None


class BusinessRule(BaseModel):
    rule: str
    applies_to: str
    condition: str


class ArchitectureSpec(BaseModel):
    entities: List[ArchEntity] = Field(..., min_length=1)
    permission_matrix: List[PermissionRule] = Field(..., min_length=1)
    page_flow: List[Page] = Field(..., min_length=1)
    business_rules: List[BusinessRule] = Field(default_factory=list)


# ─────────────────────────────────────────────
# STAGE 3 — Schema Generation (4 sub-schemas)
# ─────────────────────────────────────────────

# ── 3a. UI Schema ──

class DataBinding(BaseModel):
    endpoint_id: str        # references APIEndpoint.id
    field: Optional[str] = None          # specific field within response
    display_as: Literal["text", "table", "chart", "badge", "form", "list", "card", "image"] = "text"


class UIComponent(BaseModel):
    id: str
    type: Literal["page", "section", "card", "table", "form", "button",
                  "chart", "badge", "nav", "sidebar", "modal", "input", "select"]
    label: Optional[str] = None
    children: Optional[List["UIComponent"]] = None
    data_binding: Optional[DataBinding] = None
    actions: Optional[List[str]] = None    # endpoint ids this component can call
    roles_visible: Optional[List[str]] = None


class UISchema(BaseModel):
    pages: List[UIComponent] = Field(..., min_length=1)


# ── 3b. API Schema ──

class APIField(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "object", "array", "uuid", "datetime", "enum"]
    required: bool = True
    description: Optional[str] = None
    enum_values: Optional[List[str]] = None


class APIEndpoint(BaseModel):
    id: str                         # unique, e.g. "GET_users", "POST_contacts"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str                       # e.g. "/api/users/{id}"
    description: str
    request_body: Optional[List[APIField]] = None
    path_params: Optional[List[APIField]] = None
    query_params: Optional[List[APIField]] = None
    response_fields: List[APIField] = Field(..., min_length=1)
    auth_required: bool = True
    roles_allowed: List[str] = Field(..., min_length=1)
    db_tables: List[str] = Field(..., min_length=1)  # DB tables this endpoint touches


class APISchema(BaseModel):
    base_path: str = "/api"
    endpoints: List[APIEndpoint] = Field(..., min_length=1)


# ── 3c. DB Schema ──

DB_COL_TYPES = Literal[
    "TEXT", "INTEGER", "REAL", "BLOB",
    "BOOLEAN", "TIMESTAMP", "UUID", "JSON"
]


class DBColumn(BaseModel):
    name: str
    type: DB_COL_TYPES
    nullable: bool = False
    primary_key: bool = False
    foreign_key: Optional[str] = None       # "other_table.column"
    unique: bool = False
    default: Optional[Any] = None           # accept any LLM output, coerced to str below

    @field_validator("default", mode="before")
    @classmethod
    def coerce_default_to_str(cls, v):
        if v is None:
            return v
        return str(v)


class DBIndex(BaseModel):
    name: str
    columns: List[str]
    unique: bool = False


class DBTable(BaseModel):
    name: str
    columns: List[DBColumn] = Field(..., min_length=1)
    indexes: Optional[List[DBIndex]] = None

    @field_validator("columns")
    @classmethod
    def must_have_pk(cls, v: List[DBColumn]) -> List[DBColumn]:
        if not any(c.primary_key for c in v):
            # Auto-inject id if missing
            v.insert(0, DBColumn(name="id", type="UUID", primary_key=True, nullable=False))
        return v


class DBSchema(BaseModel):
    tables: List[DBTable] = Field(..., min_length=1)


# ── 3d. Auth Schema ──

class RouteGuard(BaseModel):
    path_pattern: str               # e.g. "/api/admin/*"
    roles_allowed: List[str]
    redirect_to: str = "/login"


class AuthSchema(BaseModel):
    roles: List[str] = Field(..., min_length=1)
    permissions: List[PermissionRule] = Field(..., min_length=1)
    route_guards: List[RouteGuard] = Field(..., min_length=1)
    session_type: Literal["jwt", "session", "oauth"] = "jwt"
    providers: List[Literal["email", "google", "github", "magic_link"]] = Field(default_factory=lambda: ["email"])


# ─────────────────────────────────────────────
# STAGE 4 — Merged AppConfig
# ─────────────────────────────────────────────

class Assumption(BaseModel):
    field: str                      # e.g. "payment_provider"
    assumed_value: str              # e.g. "Stripe"
    reason: str
    can_override: bool = True
    stage: str = "refinement"


class Conflict(BaseModel):
    description: str
    resolution: str
    source: str                     # which stage detected it
    severity: Literal["info", "warning", "error"] = "warning"


class AppConfig(BaseModel):
    intent: IntentSpec
    architecture: ArchitectureSpec
    ui_schema: UISchema
    api_schema: APISchema
    db_schema: DBSchema
    auth_schema: AuthSchema
    assumptions: List[Assumption] = Field(default_factory=list)
    conflicts: List[Conflict] = Field(default_factory=list)
    generated_at: str
    pipeline_version: str = "1.0.0"
    run_id: str = ""


# ─────────────────────────────────────────────
# VALIDATION RESULTS
# ─────────────────────────────────────────────

class ValidationResult(BaseModel):
    stage: str
    valid: bool
    syntax_valid: bool = True
    structure_valid: bool = True
    semantic_valid: bool = True
    logic_valid: bool = True
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    semantic_errors: List[str] = Field(default_factory=list)
    logic_errors: List[str] = Field(default_factory=list)


# ─────────────────────────────────────────────
# EXECUTION RESULTS
# ─────────────────────────────────────────────

class DBExecutionResult(BaseModel):
    tables_created: List[str] = Field(default_factory=list)
    tables_failed: List[dict] = Field(default_factory=list)
    total_tables: int = 0
    success_rate: float = 0.0
    ddl_statements: List[str] = Field(default_factory=list)


class APIValidationItem(BaseModel):
    endpoint_id: str
    method: str
    path: str
    passed: bool
    error: Optional[str] = None


class APIValidationResult(BaseModel):
    items: List[APIValidationItem] = Field(default_factory=list)
    success_rate: float = 0.0


class UIBindingItem(BaseModel):
    component_id: str
    endpoint_id: str
    resolved: bool
    error: Optional[str] = None


class UIValidationResult(BaseModel):
    items: List[UIBindingItem] = Field(default_factory=list)
    dangling_count: int = 0
    success_rate: float = 0.0


class ExecutionReport(BaseModel):
    db: DBExecutionResult
    api: APIValidationResult
    ui: UIValidationResult
    executability_score: float = 0.0
    executed_at: str = ""
    run_id: str = ""


# ─────────────────────────────────────────────
# PIPELINE RUN METADATA
# ─────────────────────────────────────────────

class StageMetrics(BaseModel):
    stage: str
    provider: str
    model: str
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    repair_iterations: int = 0
    success: bool = True
    error: Optional[str] = None


class PipelineRunResult(BaseModel):
    run_id: str
    prompt: str
    app_config: Optional[AppConfig] = None
    execution_report: Optional[ExecutionReport] = None
    stage_metrics: List[StageMetrics] = Field(default_factory=list)
    total_duration_ms: int = 0
    total_cost_usd: float = 0.0
    success: bool = True
    error: Optional[str] = None
    validation_report: Optional[ValidationResult] = None


# Allow forward refs in UIComponent
UIComponent.model_rebuild()
