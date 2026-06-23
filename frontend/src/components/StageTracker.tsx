import type { StageInfo } from '../types'

const STAGE_DEFS = [
  { key: 'intent',     label: 'Intent',     icon: '🧠' },
  { key: 'design',     label: 'Design',     icon: '🏗️' },
  { key: 'schema',     label: 'Schema ×4',  icon: '📐' },
  { key: 'refinement', label: 'Refine',     icon: '🔗' },
  { key: 'validation', label: 'Validate',   icon: '✅' },
  { key: 'execution',  label: 'Execute',    icon: '▶️' },
]

interface Props {
  stages: StageInfo[]
}

export default function StageTracker({ stages }: Props) {
  const stageMap = Object.fromEntries(stages.map(s => [s.key, s]))

  return (
    <div className="stage-tracker">
      {STAGE_DEFS.map((def, idx) => {
        const stage = stageMap[def.key]
        const status = stage?.status ?? 'idle'

        return (
          <div key={def.key} className={`stage-step ${status}`} id={`stage-step-${def.key}`}>
            <div className="stage-icon">
              {status === 'active' ? <span className="spinner" /> : def.icon}
            </div>
            <div className="stage-label">{def.label}</div>
            {stage?.duration_ms != null && (
              <div className="stage-meta">{stage.duration_ms}ms</div>
            )}
            {stage?.cost_usd != null && (
              <div className="stage-meta">${stage.cost_usd.toFixed(4)}</div>
            )}
            {stage?.repair_iterations != null && stage.repair_iterations > 0 && (
              <span className="repair-badge">🔧 ×{stage.repair_iterations}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}
