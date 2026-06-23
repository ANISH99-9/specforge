import { useState, useEffect } from 'react'

interface EvalResult {
  prompt_id: string
  category: string
  label: string
  success: number
  total_duration_ms: number
  total_cost_usd: number
  executability_score: number
  confidence: number
  ambiguity_count: number
  assumption_count: number
  conflict_count: number
  repair_iterations: number
  created_at: string
}

interface EvalSummary {
  total: number
  success_count: number
  success_rate: number
  avg_duration_ms: number
  avg_cost_usd: number
  avg_executability: number
}

export default function MetricsPage() {
  const [results, setResults] = useState<EvalResult[]>([])
  const [summary, setSummary] = useState<EvalSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('')

  const fetchResults = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/eval/results')
      const data = await res.json()
      setResults(data.results || [])
      setSummary(data.summary || null)
    } catch {
      setMessage('Failed to fetch results')
    } finally {
      setLoading(false)
    }
  }

  const triggerEval = async () => {
    setRunning(true)
    setMessage('Eval harness started in background (20 prompts)…')
    try {
      await fetch('/api/eval/run', { method: 'POST' })
      setTimeout(() => {
        fetchResults()
        setRunning(false)
        setMessage('Eval complete — results refreshed')
      }, 3000)
    } catch {
      setMessage('Failed to start eval')
      setRunning(false)
    }
  }

  useEffect(() => { fetchResults() }, [])

  const categoryColors: Record<string, string> = {
    normal:           'badge-green',
    edge_vague:       'badge-yellow',
    edge_conflicting: 'badge-red',
    edge_incomplete:  'badge-blue',
  }

  return (
    <div className="container" style={{ paddingTop: 40, paddingBottom: 60 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 32 }}>
        <div>
          <h1 style={{ fontSize: '2rem', marginBottom: 6 }}>📊 Eval Metrics</h1>
          <p className="text-secondary text-sm">
            20-prompt test dataset: 10 normal + 10 edge cases (vague / conflicting / incomplete)
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button id="refresh-results" className="btn btn-ghost" onClick={fetchResults} disabled={loading}>
            {loading ? <span className="spinner" /> : '↻'} Refresh
          </button>
          <button id="run-eval" className="btn btn-primary" onClick={triggerEval} disabled={running}>
            {running ? <><span className="spinner" /> Running…</> : '▶️ Run Eval Harness'}
          </button>
        </div>
      </div>

      {message && (
        <div className="validation-item validation-ok" style={{ marginBottom: 20 }}>{message}</div>
      )}

      {/* Summary cards */}
      {summary && (
        <div className="metrics-summary">
          <div className="metric-card">
            <div className="metric-label">Success Rate</div>
            <div className="metric-value" style={{ color: summary.success_rate > 0.8 ? 'var(--green)' : 'var(--yellow)' }}>
              {Math.round(summary.success_rate * 100)}%
            </div>
            <div className="text-xs text-muted">{summary.success_count}/{summary.total} prompts</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Avg Duration</div>
            <div className="metric-value">{(summary.avg_duration_ms / 1000).toFixed(1)}s</div>
            <div className="text-xs text-muted">per pipeline run</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Avg Cost</div>
            <div className="metric-value">${summary.avg_cost_usd.toFixed(4)}</div>
            <div className="text-xs text-muted">per run (all stages)</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Avg Executability</div>
            <div className="metric-value" style={{ color: 'var(--accent-3)' }}>
              {Math.round(summary.avg_executability * 100)}%
            </div>
            <div className="text-xs text-muted">DB + API + UI score</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Total Prompts</div>
            <div className="metric-value">{summary.total}</div>
            <div className="text-xs text-muted">10 normal + 10 edge</div>
          </div>
        </div>
      )}

      {/* Cost/tradeoff analysis */}
      <div className="glass" style={{ padding: 24, marginBottom: 28 }}>
        <h2 style={{ marginBottom: 16 }}>💰 Cost vs Quality Tradeoff</h2>
        <table className="data-table">
          <thead>
            <tr><th>Stage</th><th>Provider</th><th>Model</th><th>Cost/1M tok (in/out)</th><th>Why</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>Stage 1 — Intent Extraction</td>
              <td><span className="badge badge-blue">Google</span></td>
              <td><code className="font-mono text-xs">gemini-2.5-flash</code></td>
              <td className="font-mono text-xs">$0.075 / $0.30</td>
              <td className="text-xs text-muted">Structured intent parsing from natural language</td>
            </tr>
            <tr>
              <td>Stage 2 — System Design</td>
              <td><span className="badge badge-blue">Google</span></td>
              <td><code className="font-mono text-xs">gemini-2.5-flash</code></td>
              <td className="font-mono text-xs">$0.075 / $0.30</td>
              <td className="text-xs text-muted">Entity relations, permissions, and architecture</td>
            </tr>
            <tr>
              <td>Stage 3 — Schema Generation ×4</td>
              <td><span className="badge badge-blue">Google</span></td>
              <td><code className="font-mono text-xs">gemini-2.5-flash</code></td>
              <td className="font-mono text-xs">$0.075 / $0.30</td>
              <td className="text-xs text-muted">UI / API / DB / Auth — 4 parallel calls</td>
            </tr>
            <tr>
              <td>Stage 4 — Refinement</td>
              <td><span className="badge badge-blue">Google</span></td>
              <td><code className="font-mono text-xs">gemini-2.5-flash</code></td>
              <td className="font-mono text-xs">$0.075 / $0.30</td>
              <td className="text-xs text-muted">Cross-layer consistency merge</td>
            </tr>
            <tr>
              <td>Validation</td>
              <td><span className="badge badge-green">Code</span></td>
              <td><code className="font-mono text-xs">Pydantic</code></td>
              <td className="font-mono text-xs">—</td>
              <td className="text-xs text-muted">4-layer: syntax → structure → semantic → logic</td>
            </tr>
            <tr>
              <td>Repair Engine</td>
              <td><span className="badge badge-blue">Google</span></td>
              <td><code className="font-mono text-xs">gemini-2.5-flash</code></td>
              <td className="font-mono text-xs">$0.075 / $0.30</td>
              <td className="text-xs text-muted">Targeted JSON repair on validation failures</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Results table */}
      {results.length > 0 ? (
        <div className="glass" style={{ padding: 24 }}>
          <h2 style={{ marginBottom: 16 }}>Per-Prompt Results</h2>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th><th>Category</th><th>Label</th><th>Result</th>
                  <th>Duration</th><th>Cost</th><th>Exec</th><th>Confidence</th><th>Repairs</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i}>
                    <td><code className="font-mono text-xs">{r.prompt_id}</code></td>
                    <td><span className={`badge ${categoryColors[r.category] ?? 'badge-blue'}`}>{r.category.replace('edge_', '')}</span></td>
                    <td className="text-xs">{r.label}</td>
                    <td>
                      <span className={`badge ${r.success ? 'badge-green' : 'badge-red'}`}>
                        {r.success ? '✓ pass' : '✗ fail'}
                      </span>
                    </td>
                    <td className="font-mono text-xs">{r.total_duration_ms ? `${(r.total_duration_ms/1000).toFixed(1)}s` : '—'}</td>
                    <td className="font-mono text-xs">${r.total_cost_usd?.toFixed(4) ?? '—'}</td>
                    <td>
                      <span style={{ color: r.executability_score > 0.8 ? 'var(--green)' : r.executability_score > 0.5 ? 'var(--yellow)' : 'var(--red)' }}>
                        {r.executability_score ? `${Math.round(r.executability_score * 100)}%` : '—'}
                      </span>
                    </td>
                    <td className="font-mono text-xs">{r.confidence ? `${Math.round(r.confidence * 100)}%` : '—'}</td>
                    <td>{r.repair_iterations > 0 ? <span className="badge badge-yellow">×{r.repair_iterations}</span> : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : !loading && (
        <div className="glass" style={{ padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: '3rem', marginBottom: 12 }}>📭</div>
          <h3 style={{ color: 'var(--text-secondary)', marginBottom: 8 }}>No eval results yet</h3>
          <p className="text-sm text-muted" style={{ marginBottom: 20 }}>
            Click "Run Eval Harness" to evaluate all 20 test prompts and capture metrics.
          </p>
          <button className="btn btn-primary" onClick={triggerEval}>▶️ Run Eval Harness</button>
        </div>
      )}
    </div>
  )
}
