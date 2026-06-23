import { useState } from 'react'
import CompilerPage from './components/CompilerPage'
import MetricsPage from './components/MetricsPage'

type Page = 'compiler' | 'metrics'

export default function App() {
  const [page, setPage] = useState<Page>('compiler')

  return (
    <div style={{ minHeight: '100vh' }}>
      {/* Navigation */}
      <nav className="nav">
        <div className="nav-logo">
          <div className="nav-logo-icon">⚡</div>
          <span className="gradient-text">SpecForge</span>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginLeft: 4 }}>v1.0</span>
        </div>
        <div className="nav-tabs">
          <button
            id="nav-compiler"
            className={`nav-tab ${page === 'compiler' ? 'active' : ''}`}
            onClick={() => setPage('compiler')}
          >
            🔧 Compiler
          </button>
          <button
            id="nav-metrics"
            className={`nav-tab ${page === 'metrics' ? 'active' : ''}`}
            onClick={() => setPage('metrics')}
          >
            📊 Metrics
          </button>
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          NL → Spec Compiler
        </div>
      </nav>

      {/* Content */}
      {page === 'compiler' ? <CompilerPage /> : <MetricsPage />}
    </div>
  )
}
