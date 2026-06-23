// SpecForge — Shared TypeScript Types

export type StageStatus = 'idle' | 'active' | 'complete' | 'error';

export interface StageInfo {
  key: string;
  label: string;
  icon: string;
  status: StageStatus;
  duration_ms?: number;
  cost_usd?: number;
  repair_iterations?: number;
  result?: unknown;
}

export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}

export interface Assumption {
  field: string;
  assumed_value: string;
  reason: string;
  can_override: boolean;
  stage: string;
}

export interface Conflict {
  description: string;
  resolution: string;
  source: string;
  severity: 'info' | 'warning' | 'error';
}

export interface DBColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  foreign_key?: string;
  unique: boolean;
  default?: string;
}

export interface DBTable {
  name: string;
  columns: DBColumn[];
  indexes?: { name: string; columns: string[]; unique: boolean }[];
}

export interface DBSchema { tables: DBTable[] }

export interface APIField {
  name: string;
  type: string;
  required: boolean;
  description?: string;
}

export interface APIEndpoint {
  id: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  path: string;
  description: string;
  request_body?: APIField[];
  response_fields: APIField[];
  auth_required: boolean;
  roles_allowed: string[];
  db_tables: string[];
}

export interface APISchema {
  base_path: string;
  endpoints: APIEndpoint[];
}

export interface UIComponent {
  id: string;
  type: string;
  label?: string;
  children?: UIComponent[];
  data_binding?: { endpoint_id: string; display_as: string };
  actions?: string[];
  roles_visible?: string[];
}

export interface UISchema { pages: UIComponent[] }

export interface AuthSchema {
  roles: string[];
  permissions: { role: string; action: string; entity: string; allowed: boolean }[];
  route_guards: { path_pattern: string; roles_allowed: string[]; redirect_to: string }[];
  session_type: string;
  providers: string[];
}

export interface AppConfig {
  intent: {
    app_name: string;
    app_type: string;
    entities: { name: string; fields_hint: string[] }[];
    roles: string[];
    features: string[];
    monetization: { has_premium_plan: boolean; gating_hint?: string };
    ambiguities: string[];
    confidence: number;
  };
  architecture: {
    entities: { name: string; fields: unknown[] }[];
    permission_matrix: unknown[];
    page_flow: { name: string; path: string; roles_allowed: string[] }[];
    business_rules: unknown[];
  };
  ui_schema: UISchema;
  api_schema: APISchema;
  db_schema: DBSchema;
  auth_schema: AuthSchema;
  assumptions: Assumption[];
  conflicts: Conflict[];
  generated_at: string;
  run_id: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  semantic_valid: boolean;
  logic_valid: boolean;
  broken_refs?: unknown[];
}

export interface DBExecutionResult {
  tables_created: string[];
  tables_failed: { table: string; error: string }[];
  total_tables: number;
  success_rate: number;
  ddl_statements: string[];
}

export interface ExecutionReport {
  db: DBExecutionResult;
  api: { items: { endpoint_id: string; method: string; path: string; passed: boolean; error?: string }[]; success_rate: number };
  ui: { items: unknown[]; dangling_count: number; success_rate: number };
  executability_score: number;
  executed_at: string;
}

export interface StageMetrics {
  stage: string;
  provider: string;
  model: string;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  repair_iterations: number;
  success: boolean;
}

export interface PipelineState {
  running: boolean;
  stages: StageInfo[];
  appConfig: AppConfig | null;
  validation: ValidationResult | null;
  executionReport: ExecutionReport | null;
  stageMetrics: StageMetrics[];
  totalDurationMs: number;
  totalCostUsd: number;
  error: string | null;
  runId: string | null;
  clarification: { message: string; ambiguities: string[]; confidence: number } | null;
}
