import { useCallback, useEffect, useState } from 'react'
import {
  fetchAllProjects,
  fetchLatestImport,
  fetchMonths,
  fetchTotals,
  type ImportRun,
  type MonthColumn,
  type Project,
  type TotalsResponse,
} from './api'
import ForecastGrid from './components/ForecastGrid'
import ImportStatus from './components/ImportStatus'
import ProjectDetailPanel from './components/ProjectDetailPanel'

type Health = { status: string; service: string }
type HealthState =
  | { kind: 'loading' }
  | { kind: 'ready'; health: Health }
  | { kind: 'error' }

type Forecast = {
  projects: Project[]
  months: MonthColumn[]
  totals: TotalsResponse
  latestImport: ImportRun | null
}
type ForecastState =
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'ready'; forecast: Forecast }

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>({ kind: 'loading' })
  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/health', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Health check failed: ${response.status}`)
        return response.json() as Promise<Health>
      })
      .then((health) => setState({ kind: 'ready', health }))
      .catch((error: unknown) => {
        if (!isAbort(error)) setState({ kind: 'error' })
      })
    return () => controller.abort()
  }, [])
  return state
}

function useForecast(): [ForecastState, () => void] {
  const [state, setState] = useState<ForecastState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    Promise.all([
      fetchAllProjects(signal),
      fetchMonths(signal),
      fetchTotals(signal),
      fetchLatestImport(signal),
    ])
      .then(([projects, months, totals, latestImport]) =>
        setState({
          kind: 'ready',
          forecast: { projects, months: months.months, totals, latestImport },
        }),
      )
      .catch((error: unknown) => {
        if (!isAbort(error)) setState({ kind: 'error' })
      })
    return () => controller.abort()
  }, [attempt])

  const retry = useCallback(() => {
    setState({ kind: 'loading' })
    setAttempt((n) => n + 1)
  }, [])
  return [state, retry]
}

function HealthIndicator({ state }: { state: HealthState }) {
  return (
    <div className="d-flex align-items-center gap-2 small" role="status" aria-live="polite">
      {state.kind === 'loading' && (
        <>
          <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
          <span>Connecting to the API…</span>
        </>
      )}
      {state.kind === 'ready' && (
        <>
          <span className="status-dot status-dot-ready" aria-hidden="true" />
          <span>API connected ({state.health.status})</span>
        </>
      )}
      {state.kind === 'error' && (
        <>
          <span className="status-dot status-dot-error" aria-hidden="true" />
          <span className="text-danger">Unable to reach the API. Is FastAPI running?</span>
        </>
      )}
    </div>
  )
}

export default function App() {
  const health = useHealth()
  const [forecastState, retry] = useForecast()
  const [quickFilter, setQuickFilter] = useState('')
  const [selected, setSelected] = useState<Project | null>(null)

  return (
    <div className="app-page">
      <header className="app-header">
        <div>
          <p className="eyebrow mb-1">BIG Revenue Forecasting</p>
          <h1 className="h3 fw-semibold mb-0">The Chosen One</h1>
        </div>
        <HealthIndicator state={health} />
      </header>

      <main className="app-main">
        {forecastState.kind === 'loading' && (
          <div className="state-box" role="status" aria-live="polite">
            <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
            <span>Loading the forecast…</span>
          </div>
        )}

        {forecastState.kind === 'error' && (
          <div className="state-box" role="alert">
            <p className="mb-2">The forecast could not be loaded.</p>
            <p className="text-secondary small mb-3">
              Check that the API is running and reachable from this environment.
            </p>
            <button type="button" className="btn btn-primary btn-sm" onClick={retry}>
              Try again
            </button>
          </div>
        )}

        {forecastState.kind === 'ready' && forecastState.forecast.projects.length === 0 && (
          <div className="state-box">
            <p className="mb-2">No projects have been imported yet.</p>
            <p className="text-secondary small mb-0">
              Run <code>uv run python -m app.migration</code> from the <code>backend</code> folder.
            </p>
          </div>
        )}

        {forecastState.kind === 'ready' && forecastState.forecast.projects.length > 0 && (
          <>
            {forecastState.forecast.latestImport && (
              <ImportStatus run={forecastState.forecast.latestImport} />
            )}
            <div className="toolbar">
              <input
                type="search"
                className="form-control form-control-sm"
                placeholder="Search customer or project"
                aria-label="Search customer or project"
                value={quickFilter}
                onChange={(event) => setQuickFilter(event.target.value)}
              />
              <span className="text-secondary small">
                {forecastState.forecast.projects.length} projects · read-only
              </span>
            </div>
            <div className="workspace">
              <ForecastGrid
                projects={forecastState.forecast.projects}
                months={forecastState.forecast.months}
                totals={forecastState.forecast.totals}
                quickFilter={quickFilter}
                onOpenProject={setSelected}
                renderAll={import.meta.env.MODE === 'test'}
              />
              {selected && (
                <ProjectDetailPanel
                  key={selected.id}
                  project={selected}
                  onClose={() => setSelected(null)}
                />
              )}
            </div>
          </>
        )}
      </main>
    </div>
  )
}
