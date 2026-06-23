import { useState } from 'react'
import type { AppConfig, ValidationResult, ExecutionReport, StageMetrics } from '../types'

interface Props {
  appConfig: AppConfig
  validation: ValidationResult | null
  executionReport: ExecutionReport | null
  stageMetrics: StageMetrics[]
  totalCostUsd: number
  totalDurationMs: number
}

type Tab = 'intent' | 'ui' | 'api' | 'db' | 'auth' | 'assumptions' | 'validation' | 'execution' | 'metrics' | 'raw'

function ScoreCircle({ score }: { score: number }) {
  const r = 48, cx = 60, cy = 60
  const circ = 2 * Math.PI * r
  const filled = circ * score
  const color = score > 0.8 ? 'var(--green)' : score > 0.5 ? 'var(--yellow)' : 'var(--red)'
  return (
    <div className="score-circle" style={{ width: 120, height: 120 }}>
      <svg viewBox="0 0 120 120" width="120" height="120">
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="10" />
        <circle
          cx={cx} cy={cy} r={r} fill="none"
          stroke={color} strokeWidth="10"
          strokeDasharray={`${filled} ${circ - filled}`}
          strokeLinecap="round"
          style={{ transition: 'stroke-dasharray 0.8s ease' }}
        />
      </svg>
      <div style={{ position: 'absolute', textAlign: 'center' }}>
        <div className="score-value" style={{ color }}>{Math.round(score * 100)}%</div>
        <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>score</div>
      </div>
    </div>
  )
}

function UITree({ components, depth = 0 }: { components: AppConfig['ui_schema']['pages'], depth?: number }) {
  const iconMap: Record<string, string> = {
    page: '📄', section: '📦', card: '🃏', table: '📊', form: '📝',
    button: '🔘', chart: '📈', badge: '🏷️', nav: '🧭', sidebar: '☰',
    modal: '🪟', input: '✏️', select: '▼',
  }
  return (
    <>
      {components.map(comp => (
        <div key={comp.id} style={{ marginLeft: depth * 16 }}>
          <div className="tree-item">
            <span className="tree-icon">{iconMap[comp.type] ?? '🔷'}</span>
            <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{comp.label || comp.id}</span>
            <span className="badge badge-blue" style={{ marginLeft: 4 }}>{comp.type}</span>
            {comp.data_binding && (
              <span className="badge badge-purple">→ {comp.data_binding.endpoint_id}</span>
            )}
          </div>
          {comp.children && <UITree components={comp.children} depth={depth + 1} />}
        </div>
      ))}
    </>
  )
}

export default function ResultsTabs({ appConfig, validation, executionReport, stageMetrics, totalCostUsd, totalDurationMs }: Props) {
  const [tab, setTab] = useState<Tab>('intent')

  const tabs: { key: Tab; label: string; icon: string }[] = [
    { key: 'intent',      label: 'Intent',      icon: '🧠' },
    { key: 'ui',          label: 'UI Schema',   icon: '🖼️' },
    { key: 'api',         label: 'API Schema',  icon: '🔌' },
    { key: 'db',          label: 'DB Schema',   icon: '🗄️' },
    { key: 'auth',        label: 'Auth',        icon: '🔐' },
    { key: 'assumptions', label: 'Assumptions', icon: '💡' },
    { key: 'validation',  label: 'Validation',  icon: '✅' },
    { key: 'execution',   label: 'Execution',   icon: '▶️' },
    { key: 'metrics',     label: 'Metrics',     icon: '📊' },
    { key: 'raw',         label: 'Raw JSON',    icon: '{ }' },
  ]

  return (
    <div className="glass" style={{ padding: '0 0 24px', marginTop: 24 }}>
      {/* Tab bar */}
      <div className="tabs" style={{ padding: '0 20px' }}>
        {tabs.map(t => (
          <button
            key={t.key}
            id={`tab-${t.key}`}
            className={`tab-btn ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.icon} {t.label}
            {t.key === 'assumptions' && appConfig.assumptions.length > 0 && (
              <span className="badge badge-yellow" style={{ marginLeft: 4 }}>{appConfig.assumptions.length}</span>
            )}
            {t.key === 'validation' && validation && !validation.valid && (
              <span className="badge badge-red" style={{ marginLeft: 4 }}>{validation.errors.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab panels */}
      <div className="tab-panel" style={{ padding: '20px 24px 0' }}>
        {/* ── Intent ─────────────────────────────────────── */}
        {tab === 'intent' && (
          <div>
            <div className="info-grid" style={{ marginBottom: 20 }}>
              <div className="info-card">
                <div className="info-card-label">App Name</div>
                <div className="info-card-value" style={{ fontSize: '1rem' }}>{appConfig.intent.app_name}</div>
              </div>
              <div className="info-card">
                <div className="info-card-label">App Type</div>
                <div className="info-card-value" style={{ fontSize: '1rem' }}>{appConfig.intent.app_type}</div>
              </div>
              <div className="info-card">
                <div className="info-card-label">Confidence</div>
                <div className="info-card-value" style={{ color: appConfig.intent.confidence > 0.7 ? 'var(--green)' : 'var(--yellow)' }}>
                  {Math.round(appConfig.intent.confidence * 100)}%
                </div>
              </div>
              <div className="info-card">
                <div className="info-card-label">Premium Plan</div>
                <div className="info-card-value" style={{ fontSize: '1rem' }}>
                  {appConfig.intent.monetization.has_premium_plan ? '✅ Yes' : '❌ No'}
                </div>
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <div>
                <h3 style={{ marginBottom: 10, color: 'var(--text-secondary)' }}>Entities</h3>
                {appConfig.intent.entities.map(e => (
                  <div key={e.name} className="info-card" style={{ marginBottom: 8 }}>
                    <div style={{ fontWeight: 600 }}>{e.name}</div>
                    <div className="text-xs text-muted" style={{ marginTop: 4 }}>{e.fields_hint.join(' · ')}</div>
                  </div>
                ))}
              </div>
              <div>
                <h3 style={{ marginBottom: 10, color: 'var(--text-secondary)' }}>Roles & Features</h3>
                <div style={{ marginBottom: 12 }}>
                  {appConfig.intent.roles.map(r => <span key={r} className="badge badge-purple" style={{ marginRight: 6, marginBottom: 4 }}>{r}</span>)}
                </div>
                <div>
                  {appConfig.intent.features.map(f => <span key={f} className="badge badge-blue" style={{ marginRight: 6, marginBottom: 4 }}>{f}</span>)}
                </div>
                {appConfig.intent.ambiguities.length > 0 && (
                  <div style={{ marginTop: 16 }}>
                    <h3 style={{ marginBottom: 8, color: 'var(--yellow)' }}>⚠️ Ambiguities</h3>
                    {appConfig.intent.ambiguities.map((a, i) => (
                      <div key={i} className="validation-item validation-warning">{a}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── UI Schema ───────────────────────────────────── */}
        {tab === 'ui' && (
          <div>
            <div style={{ marginBottom: 12, color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              {appConfig.ui_schema.pages.length} pages in component tree
            </div>
            <div className="glass" style={{ padding: 16 }}>
              <UITree components={appConfig.ui_schema.pages} />
            </div>
          </div>
        )}

        {/* ── API Schema ──────────────────────────────────── */}
        {tab === 'api' && (
          <div>
            <div style={{ marginBottom: 12, color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              {appConfig.api_schema.endpoints.length} endpoints · base: {appConfig.api_schema.base_path}
            </div>
            {appConfig.api_schema.endpoints.map(ep => (
              <div key={ep.id} className="endpoint-card">
                <span className={`method-badge method-${ep.method}`}>{ep.method}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <code className="font-mono" style={{ fontSize: '0.85rem', color: 'var(--text-primary)' }}>{ep.path}</code>
                    {ep.auth_required && <span className="badge badge-purple" style={{ fontSize: '0.65rem' }}>🔐 auth</span>}
                  </div>
                  <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{ep.description}</div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {ep.roles_allowed.map(r => <span key={r} className="badge badge-blue" style={{ fontSize: '0.65rem' }}>{r}</span>)}
                    {ep.db_tables.map(t => <span key={t} className="badge badge-purple" style={{ fontSize: '0.65rem' }}>🗄️ {t}</span>)}
                  </div>
                </div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  {ep.response_fields.length} resp fields
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ── DB Schema ───────────────────────────────────── */}
        {tab === 'db' && (
          <div>
            {appConfig.db_schema.tables.map(table => (
              <div key={table.name} style={{ marginBottom: 20 }}>
                <h3 style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                  🗄️ {table.name}
                  <span className="text-muted text-xs">({table.columns.length} columns)</span>
                </h3>
                <div style={{ overflowX: 'auto' }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Column</th><th>Type</th><th>Constraints</th><th>Default</th><th>FK</th>
                      </tr>
                    </thead>
                    <tbody>
                      {table.columns.map(col => (
                        <tr key={col.name}>
                          <td>
                            <code className="font-mono" style={{ color: 'var(--text-primary)' }}>{col.name}</code>
                            {col.primary_key && <span className="badge badge-yellow" style={{ marginLeft: 6, fontSize: '0.65rem' }}>PK</span>}
                          </td>
                          <td><span className="badge badge-blue">{col.type}</span></td>
                          <td>
                            {col.unique && <span className="badge badge-purple" style={{ marginRight: 4 }}>UNIQUE</span>}
                            {!col.nullable && !col.primary_key && <span className="badge badge-red">NOT NULL</span>}
                            {col.nullable && <span className="badge badge-green">nullable</span>}
                          </td>
                          <td><code className="font-mono text-xs">{col.default ?? '—'}</code></td>
                          <td><code className="font-mono text-xs" style={{ color: 'var(--accent-3)' }}>{col.foreign_key ?? '—'}</code></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {table.indexes && table.indexes.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    {table.indexes.map(idx => (
                      <span key={idx.name} className="badge badge-purple" style={{ marginRight: 6 }}>
                        {idx.unique ? '🔑' : '📌'} {idx.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* ── Auth Schema ─────────────────────────────────── */}
        {tab === 'auth' && (
          <div>
            <div className="info-grid" style={{ marginBottom: 20 }}>
              <div className="info-card">
                <div className="info-card-label">Session Type</div>
                <div className="info-card-value" style={{ fontSize: '1rem' }}>{appConfig.auth_schema.session_type.toUpperCase()}</div>
              </div>
              <div className="info-card">
                <div className="info-card-label">Providers</div>
                <div className="info-card-value" style={{ fontSize: '1rem' }}>{appConfig.auth_schema.providers.join(', ')}</div>
              </div>
              <div className="info-card">
                <div className="info-card-label">Roles</div>
                <div style={{ marginTop: 8 }}>
                  {appConfig.auth_schema.roles.map(r => <span key={r} className="badge badge-purple" style={{ marginRight: 4 }}>{r}</span>)}
                </div>
              </div>
            </div>
            <h3 style={{ marginBottom: 10 }}>Route Guards</h3>
            <table className="data-table" style={{ marginBottom: 20 }}>
              <thead><tr><th>Pattern</th><th>Roles Allowed</th><th>Redirect</th></tr></thead>
              <tbody>
                {appConfig.auth_schema.route_guards.map((g, i) => (
                  <tr key={i}>
                    <td><code className="font-mono">{g.path_pattern}</code></td>
                    <td>{g.roles_allowed.map(r => <span key={r} className="badge badge-blue" style={{ marginRight: 4 }}>{r}</span>)}</td>
                    <td><code className="font-mono text-xs">{g.redirect_to}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3 style={{ marginBottom: 10 }}>Permission Matrix</h3>
            <table className="data-table">
              <thead><tr><th>Role</th><th>Action</th><th>Entity</th><th>Allowed</th><th>Condition</th></tr></thead>
              <tbody>
                {appConfig.auth_schema.permissions.slice(0, 20).map((p, i) => (
                  <tr key={i}>
                    <td><span className="badge badge-purple">{p.role}</span></td>
                    <td><code className="font-mono text-xs">{p.action}</code></td>
                    <td>{p.entity}</td>
                    <td>{p.allowed ? <span className="badge badge-green">✓</span> : <span className="badge badge-red">✗</span>}</td>
                    <td><code className="font-mono text-xs">{(p as { condition?: string }).condition ?? '—'}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* ── Assumptions ─────────────────────────────────── */}
        {tab === 'assumptions' && (
          <div>
            {appConfig.assumptions.length === 0 ? (
              <div className="validation-item validation-ok">✅ No assumptions needed — prompt was fully specified</div>
            ) : (
              <>
                <p className="text-sm text-muted" style={{ marginBottom: 16 }}>
                  SpecForge made {appConfig.assumptions.length} assumption(s) to fill gaps in your prompt.
                  These are documented for full transparency.
                </p>
                {appConfig.assumptions.map((a, i) => (
                  <div key={i} className="assumption-card">
                    <span style={{ fontSize: '1.2rem' }}>💡</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        <strong>{a.field}</strong>
                        <span style={{ color: 'var(--yellow)' }}>→</span>
                        <code className="font-mono" style={{ color: 'var(--accent-3)', fontSize: '0.85rem' }}>{a.assumed_value}</code>
                        {a.can_override && <span className="badge badge-yellow">overridable</span>}
                      </div>
                      <div className="text-sm text-muted">{a.reason}</div>
                      <div className="text-xs" style={{ marginTop: 4, color: 'var(--text-muted)' }}>Source: {a.stage}</div>
                    </div>
                  </div>
                ))}
                {appConfig.conflicts.length > 0 && (
                  <>
                    <div className="divider" />
                    <h3 style={{ marginBottom: 12, color: 'var(--yellow)' }}>⚠️ Conflicts Resolved</h3>
                    {appConfig.conflicts.map((c, i) => (
                      <div key={i} className="assumption-card" style={{ background: 'rgba(239,68,68,0.06)', borderColor: 'rgba(239,68,68,0.2)' }}>
                        <span style={{ fontSize: '1.2rem' }}>⚡</span>
                        <div>
                          <div style={{ fontWeight: 600, color: 'var(--red)', marginBottom: 4 }}>{c.description}</div>
                          <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>Resolution: {c.resolution}</div>
                          <div className="text-xs text-muted" style={{ marginTop: 4 }}>Detected by: {c.source}</div>
                        </div>
                      </div>
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        )}

        {/* ── Validation ──────────────────────────────────── */}
        {tab === 'validation' && (
          <div>
            {validation ? (
              <>
                <div className="info-grid" style={{ marginBottom: 20 }}>
                  <div className="info-card">
                    <div className="info-card-label">Overall</div>
                    <div className="info-card-value">{validation.valid ? '✅ Valid' : '❌ Issues'}</div>
                  </div>
                  <div className="info-card">
                    <div className="info-card-label">Semantic</div>
                    <div className="info-card-value">{validation.semantic_valid ? '✅' : '❌'}</div>
                  </div>
                  <div className="info-card">
                    <div className="info-card-label">Logic</div>
                    <div className="info-card-value">{validation.logic_valid ? '✅' : '❌'}</div>
                  </div>
                  <div className="info-card">
                    <div className="info-card-label">Warnings</div>
                    <div className="info-card-value" style={{ color: 'var(--yellow)' }}>{validation.warnings.length}</div>
                  </div>
                </div>
                {validation.errors.length === 0 && validation.warnings.length === 0 ? (
                  <div className="validation-item validation-ok">✅ All 4 validation layers passed — syntax, structure, semantic, logic</div>
                ) : (
                  <>
                    {validation.errors.map((e, i) => <div key={i} className="validation-item validation-error">❌ {e}</div>)}
                    {validation.warnings.map((w, i) => <div key={i} className="validation-item validation-warning">⚠️ {w}</div>)}
                  </>
                )}
              </>
            ) : <div className="text-muted">No validation data yet</div>}
          </div>
        )}

        {/* ── Execution ───────────────────────────────────── */}
        {tab === 'execution' && (
          <div>
            {executionReport ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 32, marginBottom: 24 }}>
                  <ScoreCircle score={executionReport.executability_score} />
                  <div>
                    <h2>Executability Score</h2>
                    <p className="text-sm text-muted" style={{ marginTop: 4 }}>
                      DB (50%) + API (30%) + UI Bindings (20%)
                    </p>
                    <div style={{ display: 'flex', gap: 16, marginTop: 12 }}>
                      <div>
                        <div className="text-xs text-muted">DB Tables</div>
                        <div style={{ fontWeight: 700, color: executionReport.db.success_rate === 1 ? 'var(--green)' : 'var(--yellow)' }}>
                          {executionReport.db.tables_created.length}/{executionReport.db.total_tables}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-muted">API Endpoints</div>
                        <div style={{ fontWeight: 700, color: executionReport.api.success_rate === 1 ? 'var(--green)' : 'var(--yellow)' }}>
                          {Math.round(executionReport.api.success_rate * 100)}%
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-muted">UI Bindings</div>
                        <div style={{ fontWeight: 700, color: executionReport.ui.success_rate === 1 ? 'var(--green)' : 'var(--yellow)' }}>
                          {Math.round(executionReport.ui.success_rate * 100)}%
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <h3 style={{ marginBottom: 10 }}>DB Tables Created (SQLite)</h3>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
                  {executionReport.db.tables_created.map(t => (
                    <span key={t} className="badge badge-green">🗄️ {t}</span>
                  ))}
                  {executionReport.db.tables_failed.map((f, i) => (
                    <span key={i} className="badge badge-red" title={f.error}>❌ {f.table}</span>
                  ))}
                </div>

                {executionReport.db.tables_failed.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <h3 style={{ marginBottom: 8, color: 'var(--red)' }}>Failed Tables</h3>
                    {executionReport.db.tables_failed.map((f, i) => (
                      <div key={i} className="validation-item validation-error">
                        <strong>{f.table}</strong>: {f.error}
                      </div>
                    ))}
                  </div>
                )}

                {executionReport.db.ddl_statements.length > 0 && (
                  <>
                    <h3 style={{ marginBottom: 8 }}>DDL Statements</h3>
                    <div className="code-block">{executionReport.db.ddl_statements.join('\n\n')}</div>
                  </>
                )}
              </>
            ) : (
              <div className="text-muted">Execution report not available</div>
            )}
          </div>
        )}

        {/* ── Metrics ─────────────────────────────────────── */}
        {tab === 'metrics' && (
          <div>
            <div className="info-grid" style={{ marginBottom: 20 }}>
              <div className="info-card">
                <div className="info-card-label">Total Duration</div>
                <div className="info-card-value">{(totalDurationMs / 1000).toFixed(1)}s</div>
              </div>
              <div className="info-card">
                <div className="info-card-label">Total Cost</div>
                <div className="info-card-value">${totalCostUsd.toFixed(5)}</div>
              </div>
              <div className="info-card">
                <div className="info-card-label">Total Repairs</div>
                <div className="info-card-value">{stageMetrics.reduce((a, m) => a + m.repair_iterations, 0)}</div>
              </div>
              <div className="info-card">
                <div className="info-card-label">Stages</div>
                <div className="info-card-value">{stageMetrics.length}</div>
              </div>
            </div>
            <h3 style={{ marginBottom: 10 }}>Per-Stage Breakdown</h3>
            <table className="data-table">
              <thead>
                <tr><th>Stage</th><th>Provider</th><th>Model</th><th>Duration</th><th>Tokens</th><th>Cost</th><th>Repairs</th></tr>
              </thead>
              <tbody>
                {stageMetrics.map((m, i) => (
                  <tr key={i}>
                    <td>{m.stage}</td>
                    <td><span className="badge badge-blue">{m.provider}</span></td>
                    <td><code className="font-mono text-xs">{m.model}</code></td>
                    <td>{m.duration_ms}ms</td>
                    <td className="font-mono text-xs">{m.input_tokens}+{m.output_tokens}</td>
                    <td>${m.cost_usd.toFixed(5)}</td>
                    <td>{m.repair_iterations > 0 ? <span className="badge badge-yellow">×{m.repair_iterations}</span> : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* ── Raw JSON ────────────────────────────────────── */}
        {tab === 'raw' && (
          <div>
            <div className="code-block">{JSON.stringify(appConfig, null, 2)}</div>
          </div>
        )}
      </div>
    </div>
  )
}
