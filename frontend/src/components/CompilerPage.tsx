import { useState, useRef, useCallback } from 'react'
import type { PipelineState, StageInfo, AppConfig, ValidationResult, ExecutionReport, StageMetrics } from '../types'
import StageTracker from './StageTracker'
import ResultsTabs from './ResultsTabs'

const STAGE_KEYS = ['intent', 'design', 'schema', 'refinement', 'validation', 'execution']

const EXAMPLE_PROMPTS = [
  "Build a CRM for a B2B sales team. Sales reps manage contacts, companies, and deals through a pipeline. Managers see team analytics. Free tier: 100 contacts. Pro plan ($29/mo): unlimited + analytics.",
  "Build a two-sided marketplace for freelance designers. Clients post projects, designers bid, platform takes 10% fee. Stripe Connect payments. Admin moderates content.",
  "Build an appointment booking platform for salons. Owners set services and availability, customers book and pay. SMS reminders via Twilio.",
]

function initState(): PipelineState {
  return {
    running: false,
    stages: STAGE_KEYS.map(key => ({ key, label: key, icon: '', status: 'idle' })),
    appConfig: null,
    validation: null,
    executionReport: null,
    stageMetrics: [],
    totalDurationMs: 0,
    totalCostUsd: 0,
    error: null,
    runId: null,
    clarification: null,
  }
}

export default function CompilerPage() {
  const [prompt, setPrompt] = useState('')
  const [rerunPrompt, setRerunPrompt] = useState('')
  const [state, setState] = useState<PipelineState>(initState)
  const abortRef = useRef<AbortController | null>(null)

  const updateStage = useCallback((key: string, update: Partial<StageInfo>) => {
    setState(prev => ({
      ...prev,
      stages: prev.stages.map(s => s.key === key ? { ...s, ...update } : s),
    }))
  }, [])

  const runPipeline = useCallback(async (promptText: string) => {
    if (!promptText.trim()) return
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    setState({ ...initState(), running: true })

    try {
      const resp = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: promptText, run_execution_layer: true }),
        signal: ctrl.signal,
      })

      if (!resp.ok || !resp.body) {
        setState(prev => ({ ...prev, running: false, error: `HTTP ${resp.status}` }))
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() ?? ''

        for (const chunk of lines) {
          const dataLine = chunk.split('\n').find(l => l.startsWith('data:'))
          if (!dataLine) continue
          try {
            const event = JSON.parse(dataLine.slice(5).trim())
            handleEvent(event)
          } catch { /* ignore parse errors */ }
        }
      }
    } catch (err: unknown) {
      if ((err as { name?: string }).name !== 'AbortError') {
        setState(prev => ({ ...prev, running: false, error: String(err) }))
      }
    }

    function handleEvent(ev: Record<string, unknown>) {
      switch (ev.type) {
        case 'run_start':
          setState(prev => ({ ...prev, runId: ev.run_id as string }))
          break

        case 'stage_start':
          updateStage(ev.stage as string, { status: 'active' })
          break

        case 'stage_complete':
          updateStage(ev.stage as string, {
            status: 'complete',
            duration_ms: ev.duration_ms as number,
            cost_usd: ev.cost_usd as number,
            repair_iterations: ev.repair_iterations as number,
          })
          break

        case 'validation_complete':
          updateStage('validation', { status: 'complete' })
          setState(prev => ({ ...prev, validation: ev as unknown as ValidationResult }))
          break

        case 'execution_complete':
          updateStage('execution', { status: 'complete' })
          setState(prev => ({ ...prev, executionReport: ev.report as unknown as ExecutionReport }))
          break

        case 'clarification_needed':
          setState(prev => ({
            ...prev,
            clarification: {
              message: ev.message as string,
              ambiguities: ev.ambiguities as string[],
              confidence: ev.confidence as number,
            }
          }))
          break

        case 'pipeline_complete':
          setState(prev => ({
            ...prev,
            running: false,
            appConfig: ev.app_config as unknown as AppConfig,
            validation: ev.validation as unknown as ValidationResult,
            executionReport: ev.execution_report as unknown as ExecutionReport,
            stageMetrics: ev.stage_metrics as unknown as StageMetrics[],
            totalDurationMs: ev.total_duration_ms as number,
            totalCostUsd: ev.total_cost_usd as number,
          }))
          // mark all remaining stages complete
          setState(prev => ({
            ...prev,
            stages: prev.stages.map(s => s.status === 'idle' ? s : { ...s, status: s.status === 'active' ? 'complete' : s.status })
          }))
          break

        case 'pipeline_error':
          setState(prev => ({
            ...prev,
            running: false,
            error: ev.error as string,
            stages: prev.stages.map(s => s.status === 'active' ? { ...s, status: 'error' } : s),
          }))
          break
      }
    }
  }, [updateStage])

  const handleSubmit = () => runPipeline(prompt)
  const handleRerun  = () => { if (rerunPrompt.trim()) runPipeline(rerunPrompt) }
  const handleStop   = () => { abortRef.current?.abort(); setState(prev => ({ ...prev, running: false })) }

  const anyResult = state.appConfig !== null

  return (
    <div className="container" style={{ paddingTop: 40, paddingBottom: 60 }}>
      {/* ── Hero ────────────────────────────────────────── */}
      <div style={{ textAlign: 'center', marginBottom: 40 }}>
        <h1 style={{ fontSize: '2.8rem', marginBottom: 12 }}>
          Natural Language{' '}
          <span className="gradient-text">→ App Spec</span>
        </h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: '1.05rem', maxWidth: 600, margin: '0 auto' }}>
          Describe any app in plain English. SpecForge compiles it into a validated, cross-consistent,
          executable specification through a 4-stage AI pipeline.
        </p>
      </div>

      {/* ── Prompt Input ──────────────────────────────── */}
      <div className="glass" style={{ padding: 24, marginBottom: 24 }}>
        <label style={{ display: 'block', marginBottom: 10, fontWeight: 600, fontSize: '0.9rem' }}>
          Describe your app
        </label>
        <textarea
          id="prompt-input"
          rows={5}
          placeholder="e.g. Build a CRM for a B2B sales team. Sales reps manage contacts and deals. Managers see analytics. Free and Pro tiers."
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          disabled={state.running}
          style={{ marginBottom: 12 }}
        />

        {/* Example prompts */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {EXAMPLE_PROMPTS.map((ex, i) => (
            <button
              key={i}
              className="btn btn-ghost btn-sm"
              onClick={() => setPrompt(ex)}
              disabled={state.running}
            >
              Example {i + 1}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button
            id="compile-btn"
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={state.running || !prompt.trim()}
            style={{ fontSize: '1rem', padding: '12px 28px' }}
          >
            {state.running ? <><span className="spinner" /> Compiling…</> : '⚡ Compile Spec'}
          </button>
          {state.running && (
            <button className="btn btn-ghost btn-sm" onClick={handleStop}>⏹ Stop</button>
          )}
          {state.runId && (
            <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem', fontFamily: 'var(--font-mono)' }}>
              run: {state.runId}
            </span>
          )}
        </div>
      </div>

      {/* ── Clarification ──────────────────────────────── */}
      {state.clarification && (
        <div className="clarification-panel">
          <div style={{ fontWeight: 700, color: 'var(--yellow)', marginBottom: 8 }}>
            ⚠️ Low Confidence ({Math.round(state.clarification.confidence * 100)}%) — Clarification Suggested
          </div>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: 8 }}>
            {state.clarification.message}
          </p>
          <div>
            {state.clarification.ambiguities.map((a, i) => (
              <div key={i} className="validation-item validation-warning" style={{ marginBottom: 4 }}>• {a}</div>
            ))}
          </div>
        </div>
      )}

      {/* ── Stage Tracker ──────────────────────────────── */}
      {(state.running || anyResult) && (
        <div className="glass" style={{ padding: '16px 24px', marginBottom: 24 }}>
          <h3 style={{ marginBottom: 4, color: 'var(--text-secondary)', fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Pipeline Progress
          </h3>
          <StageTracker stages={state.stages} />
          {!state.running && anyResult && (
            <div style={{ display: 'flex', gap: 16, marginTop: 8, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              <span className="text-xs text-muted">⏱ {(state.totalDurationMs / 1000).toFixed(1)}s total</span>
              <span className="text-xs text-muted">💰 ${state.totalCostUsd.toFixed(5)} total cost</span>
              {state.executionReport && (
                <span className="text-xs" style={{ color: state.executionReport.executability_score > 0.8 ? 'var(--green)' : 'var(--yellow)' }}>
                  ▶️ {Math.round(state.executionReport.executability_score * 100)}% executable
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Error ─────────────────────────────────────── */}
      {state.error && (
        <div className="validation-item validation-error" style={{ marginBottom: 16, borderRadius: 8, padding: '12px 16px' }}>
          ❌ {state.error}
        </div>
      )}

      {/* ── Results Tabs ───────────────────────────────── */}
      {anyResult && state.appConfig && (
        <ResultsTabs
          appConfig={state.appConfig}
          validation={state.validation}
          executionReport={state.executionReport}
          stageMetrics={state.stageMetrics}
          totalCostUsd={state.totalCostUsd}
          totalDurationMs={state.totalDurationMs}
        />
      )}

      {/* ── Re-run with Edit ───────────────────────────── */}
      {anyResult && (
        <div className="glass" style={{ padding: 24, marginTop: 24 }}>
          <h3 style={{ marginBottom: 12 }}>🔄 Re-run with Edit</h3>
          <p className="text-sm text-muted" style={{ marginBottom: 12 }}>
            Modify the prompt and re-compile to see how the spec changes.
          </p>
          <textarea
            id="rerun-input"
            rows={3}
            placeholder="Edit your prompt here and re-run…"
            value={rerunPrompt || prompt}
            onChange={e => setRerunPrompt(e.target.value)}
            disabled={state.running}
            style={{ marginBottom: 12 }}
          />
          <button
            id="rerun-btn"
            className="btn btn-ghost"
            onClick={handleRerun}
            disabled={state.running}
          >
            ⚡ Re-compile
          </button>
        </div>
      )}
    </div>
  )
}
