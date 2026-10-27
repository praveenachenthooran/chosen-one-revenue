import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  fetchAllProjects,
  fetchLatestImport,
  fetchMonths,
  fetchTotals,
  saveMonth,
  type ImportRun,
  type MonthColumn,
  type Project,
  type TotalsResponse,
} from './api'
import ForecastGrid from './components/ForecastGrid'
import ImportStatus from './components/ImportStatus'
import ProjectDetailPanel from './components/ProjectDetailPanel'
import type { SaveAmount, SaveOutcome } from './editing'
import { formatMonthLabel, parseAmountInput } from './format'

type Data = {
  projects: Project[]
  months: MonthColumn[]
  totals: TotalsResponse
  latestImport: ImportRun | null
}
type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; data: Data }
type Notice = { kind: 'success' | 'error'; text: string }

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

export default function ForecastWorkspace({
  onSessionEnded,
  initialProjectId = null,
}: {
  onSessionEnded: () => void
  initialProjectId?: number | null
}) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [quickFilter, setQuickFilter] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(initialProjectId)
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set())
  const [notice, setNotice] = useState<Notice | null>(null)
  const [revision, setRevision] = useState(0)
  const projectsRef = useRef<Project[]>([])

  const patch = useCallback((change: (data: Data) => Data) => {
    setState((current) => (current.kind === 'ready' ? { kind: 'ready', data: change(current.data) } : current))
  }, [])

  // Any 401 means the session ended; any other failure of the first load shows the error state.
  const handleFailure = useCallback(
    (error: unknown) => {
      if (isAbort(error)) return
      if (error instanceof ApiError && error.status === 401) onSessionEnded()
      else setState({ kind: 'error' })
    },
    [onSessionEnded],
  )

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
        setState({ kind: 'ready', data: { projects, months: months.months, totals, latestImport } }),
      )
      .catch(handleFailure)
    return () => controller.abort()
  }, [attempt, handleFailure])

  const projects = state.kind === 'ready' ? state.data.projects : null
  useEffect(() => {
    projectsRef.current = projects ?? []
  }, [projects])

  useEffect(() => {
    if (notice?.kind !== 'success') return
    const timer = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(timer)
  }, [notice])

  /** Re-read the projects and totals, e.g. after a conflict. */
  const reload = useCallback(async () => {
    const [fresh, totals] = await Promise.all([fetchAllProjects(), fetchTotals()])
    patch((data) => ({ ...data, projects: fresh, totals }))
  }, [patch])

  const saveAmount = useCallback<SaveAmount>(
    async (projectId, month, raw, reason): Promise<SaveOutcome> => {
      const label = formatMonthLabel(month)
      const project = projectsRef.current.find((p) => p.id === projectId)
      const plain = parseAmountInput(raw)
      if (plain === null) {
        const message = `${label}: enter a plain number such as 12500 or 12,500.50 (use 0 for no revenue). Text, formulas and negative values are not accepted.`
        setNotice({ kind: 'error', text: message })
        return { ok: false, message }
      }
      const key = `${projectId}:${month}`
      setPending((current) => new Set(current).add(key))
      try {
        const result = await saveMonth(projectId, month, plain, project?.monthly?.[month] ?? null, reason)
        patch((data) => ({
          ...data,
          projects: data.projects.map((p) =>
            p.id === projectId
              ? { ...p, total_revenue: result.project_total, monthly: { ...p.monthly, [month]: result.amount } }
              : p,
          ),
        }))
        void fetchTotals()
          .then((totals) => patch((data) => ({ ...data, totals })))
          .catch(() => undefined)
        setRevision((n) => n + 1)
        setNotice({
          kind: 'success',
          text: result.changed
            ? `Saved ${label} for ${project?.name ?? 'the project'}.`
            : `${label} was already that value; nothing changed.`,
        })
        return { ok: true, changed: result.changed }
      } catch (error) {
        let message = 'Could not save. Check your connection and try again.'
        if (error instanceof ApiError) {
          if (error.status === 401) {
            onSessionEnded()
            return { ok: false, message: 'Your session has ended.' }
          }
          message = error.message
          if (error.status === 409) {
            message = `${label}: ${error.message} The latest value has been loaded.`
            await reload().catch(() => undefined)
            setRevision((n) => n + 1)
          }
        }
        setNotice({ kind: 'error', text: message })
        return { ok: false, message }
      } finally {
        setPending((current) => {
          const next = new Set(current)
          next.delete(key)
          return next
        })
      }
    },
    [patch, reload, onSessionEnded],
  )

  const onEditAmount = useCallback(
    (projectId: number, month: string, raw: string) => void saveAmount(projectId, month, raw),
    [saveAmount],
  )

  if (state.kind === 'loading') {
    return (
      <div className="state-box" role="status" aria-live="polite">
        <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
        <span>Loading the forecast…</span>
      </div>
    )
  }

  if (state.kind === 'error') {
    return (
      <div className="state-box" role="alert">
        <p className="mb-2">The forecast could not be loaded.</p>
        <p className="text-secondary small mb-3">
          Check that the API is running and reachable from this environment.
        </p>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => {
            setState({ kind: 'loading' })
            setAttempt((n) => n + 1)
          }}
        >
          Try again
        </button>
      </div>
    )
  }

  const { data } = state
  if (data.projects.length === 0) {
    return (
      <div className="state-box">
        <p className="mb-2">No projects have been imported yet.</p>
        <p className="text-secondary small mb-0">
          Run <code>uv run python -m app.migration</code> from the <code>backend</code> folder.
        </p>
      </div>
    )
  }

  const editable = data.projects.filter((p) => p.can_edit).length
  const selected = data.projects.find((p) => p.id === selectedId) ?? null

  return (
    <>
      {data.latestImport && <ImportStatus run={data.latestImport} />}
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
          {data.projects.length} projects ·{' '}
          {editable === 0 ? 'read-only' : `you can edit ${editable} (double-click a month)`}
        </span>
        {pending.size > 0 && (
          <span className="saving-indicator" role="status" aria-live="polite">
            <span className="spinner-border spinner-border-sm" aria-hidden="true" /> Saving…
          </span>
        )}
      </div>
      {notice && (
        <div
          className={`notice notice-${notice.kind}`}
          role={notice.kind === 'error' ? 'alert' : 'status'}
        >
          <span>{notice.text}</span>
          <button type="button" className="btn-close btn-sm" aria-label="Dismiss message" onClick={() => setNotice(null)} />
        </div>
      )}
      <div className="workspace">
        <ForecastGrid
          projects={data.projects}
          months={data.months}
          totals={data.totals}
          quickFilter={quickFilter}
          onOpenProject={(project) => setSelectedId(project.id)}
          onEditAmount={onEditAmount}
          pending={pending}
          renderAll={import.meta.env.MODE === 'test'}
        />
        {selected && (
          <ProjectDetailPanel
            key={selected.id}
            project={selected}
            months={data.months}
            saveAmount={saveAmount}
            refreshToken={revision}
            onClose={() => setSelectedId(null)}
          />
        )}
      </div>
    </>
  )
}
