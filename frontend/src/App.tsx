import { useCallback, useEffect, useState } from 'react'
import { fetchAuth, signOut, type AuthUser } from './api'
import ForecastWorkspace from './ForecastWorkspace'
import PipelineView from './pipeline/PipelineView'
import QualityView from './quality/QualityView'
import AccuracyView from './reporting/AccuracyView'
import ActualsView from './reporting/ActualsView'
import SnapshotsView from './reporting/SnapshotsView'
import SignIn from './components/SignIn'

type View = 'forecast' | 'pipeline' | 'quality' | 'snapshots' | 'actuals' | 'accuracy'

// Who may see actuals and the accuracy report, and who may load actuals (the server enforces both).
const ACTUALS_VIEWERS = ['Finance', 'CRO / Sales Leadership', 'Administrator']
const ACTUALS_LOADERS = ['Finance', 'Administrator']

type Health = { status: string; service: string }
type HealthState = { kind: 'loading' } | { kind: 'ready'; health: Health } | { kind: 'error' }
type AuthState =
  | { kind: 'loading' }
  | { kind: 'unreachable' }
  | { kind: 'signedOut'; error: string | null }
  | { kind: 'signedIn'; user: AuthUser }

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

/** The sign-in error Google redirected back with (?auth_error=...), removed from the URL. */
function takeAuthError(): string | null {
  const url = new URL(window.location.href)
  const error = url.searchParams.get('auth_error')
  if (error) {
    url.searchParams.delete('auth_error')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
  }
  return error
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
  const [auth, setAuth] = useState<AuthState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [view, setView] = useState<View>('forecast')
  const [openProjectId, setOpenProjectId] = useState<number | null>(null)
  const [openDeal, setOpenDeal] = useState<{ id: number; title: string } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const authError = takeAuthError()
    fetchAuth(controller.signal)
      .then((status) =>
        setAuth(
          status.authenticated && status.user
            ? { kind: 'signedIn', user: status.user }
            : { kind: 'signedOut', error: authError },
        ),
      )
      .catch((error: unknown) => {
        if (!isAbort(error)) setAuth({ kind: 'unreachable' })
      })
    return () => controller.abort()
  }, [attempt])

  const onSessionEnded = useCallback(() => setAuth({ kind: 'signedOut', error: 'session_ended' }), [])

  async function handleSignOut() {
    try {
      await signOut()
    } finally {
      setAuth({ kind: 'signedOut', error: null })
    }
  }

  if (auth.kind === 'signedOut') {
    return (
      <>
        <SignIn error={auth.error} />
        <div className="signin-health">
          <HealthIndicator state={health} />
        </div>
      </>
    )
  }

  return (
    <div className="app-page">
      <header className="app-header">
        <div>
          <p className="eyebrow mb-1">BIG Revenue Forecasting</p>
          <h1 className="h3 fw-semibold mb-0">The Chosen One</h1>
        </div>
        <div className="d-flex align-items-center gap-3 flex-wrap justify-content-end">
          <HealthIndicator state={health} />
          {auth.kind === 'signedIn' && (
            <div className="d-flex align-items-center gap-2">
              <span className="small text-end">
                <strong>{auth.user.name}</strong>
                <br />
                <span className="text-secondary">{auth.user.roles.join(' · ') || 'No roles'}</span>
              </span>
              <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => void handleSignOut()}>
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      {auth.kind === 'signedIn' && (
        <nav className="app-tabs" role="tablist" aria-label="Views">
          {(
            [
              ['forecast', 'Forecast', true],
              ['pipeline', 'Pipeline', true],
              ['quality', 'Quality', true],
              ['snapshots', 'Snapshots', true],
              ['actuals', 'Actuals', auth.user.roles.some((r) => ACTUALS_VIEWERS.includes(r))],
              ['accuracy', 'Accuracy', auth.user.roles.some((r) => ACTUALS_VIEWERS.includes(r))],
            ] as const
          )
            .filter(([, , visible]) => visible)
            .map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`tab-${id}`}
                aria-selected={view === id}
                className={`app-tab${view === id ? ' active' : ''}`}
                onClick={() => {
                  setOpenProjectId(null)
                  setOpenDeal(null)
                  setView(id)
                }}
              >
                {label}
              </button>
            ))}
        </nav>
      )}

      <main className="app-main" role="tabpanel" aria-labelledby={`tab-${view}`}>
        {auth.kind === 'loading' && (
          <div className="state-box" role="status" aria-live="polite">
            <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
            <span>Checking your session…</span>
          </div>
        )}
        {auth.kind === 'unreachable' && (
          <div className="state-box" role="alert">
            <p className="mb-2">We could not check whether you are signed in.</p>
            <p className="text-secondary small mb-3">The API may be down. Try again in a moment.</p>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => {
                setAuth({ kind: 'loading' })
                setAttempt((n) => n + 1)
              }}
            >
              Try again
            </button>
          </div>
        )}
        {auth.kind === 'signedIn' && view === 'forecast' && (
          <ForecastWorkspace key={openProjectId ?? 'forecast'} onSessionEnded={onSessionEnded} initialProjectId={openProjectId} />
        )}
        {auth.kind === 'signedIn' && view === 'quality' && (
          <QualityView
            onSessionEnded={onSessionEnded}
            onOpenProject={(projectId) => {
              setOpenProjectId(projectId)
              setView('forecast')
            }}
            onOpenDeal={(deal) => {
              setOpenDeal(deal)
              setView('pipeline')
            }}
          />
        )}
        {auth.kind === 'signedIn' && view === 'snapshots' && <SnapshotsView onSessionEnded={onSessionEnded} />}
        {auth.kind === 'signedIn' && view === 'actuals' && (
          <ActualsView
            canUpload={auth.user.roles.some((r) => ACTUALS_LOADERS.includes(r))}
            onSessionEnded={onSessionEnded}
          />
        )}
        {auth.kind === 'signedIn' && view === 'accuracy' && <AccuracyView onSessionEnded={onSessionEnded} />}
        {auth.kind === 'signedIn' && view === 'pipeline' && (
          <PipelineView
            key={openDeal?.id ?? 'pipeline'}
            initialDeal={openDeal}
            user={auth.user}
            onSessionEnded={onSessionEnded}
            onOpenProject={(projectId) => {
              setOpenProjectId(projectId)
              setView('forecast')
            }}
          />
        )}
      </main>
    </div>
  )
}
