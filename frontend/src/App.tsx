import { useEffect, useState } from 'react'

type Health = {
  status: string
  service: string
}

type HealthState =
  | { kind: 'loading' }
  | { kind: 'ready'; health: Health }
  | { kind: 'error' }

export default function App() {
  const [healthState, setHealthState] = useState<HealthState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()

    async function checkHealth() {
      try {
        const response = await fetch('/api/health', { signal: controller.signal })
        if (!response.ok) throw new Error(`Health check failed: ${response.status}`)
        const health = (await response.json()) as Health
        setHealthState({ kind: 'ready', health })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setHealthState({ kind: 'error' })
      }
    }

    void checkHealth()
    return () => controller.abort()
  }, [])

  return (
    <div className="app-shell min-vh-100 d-flex align-items-center">
      <main className="container py-5">
        <section className="hero-card mx-auto p-4 p-md-5 rounded-4 shadow-sm">
          <p className="eyebrow mb-2">BIG Revenue Forecasting</p>
          <h1 className="display-4 fw-semibold mb-3">The Chosen One</h1>
          <p className="lead text-secondary mb-4">
            The development foundation is ready. Revenue forecasting features arrive in a later milestone.
          </p>
          <div className="d-flex align-items-center gap-2" role="status" aria-live="polite">
            {healthState.kind === 'loading' && (
              <>
                <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
                <span>Connecting to the API…</span>
              </>
            )}
            {healthState.kind === 'ready' && (
              <>
                <span className="status-dot status-dot-ready" aria-hidden="true" />
                <span>API connected ({healthState.health.status})</span>
              </>
            )}
            {healthState.kind === 'error' && (
              <>
                <span className="status-dot status-dot-error" aria-hidden="true" />
                <span className="text-danger">Unable to reach the API. Is FastAPI running?</span>
              </>
            )}
          </div>
        </section>
      </main>
    </div>
  )
}

