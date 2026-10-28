import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ApiError,
  fetchAllProjects,
  fetchFindings,
  fetchHealth,
  fetchMonths,
  saveMonth,
  type Project,
} from './api'
import type { SaveAmount, SaveOutcome } from './editing'
import { formatMonthLabel, parseAmountInput } from './format'
import { byPriority, buildRow } from './projects/status'
import { currentMonthOf, WorkspaceContext, type Data, type Notice, type State, type Workspace } from './workspaceContext'

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/** Loads the projects, months and QA findings once, and keeps them in step with every edit. */
export function WorkspaceProvider({ onSessionEnded, children }: { onSessionEnded: () => void; children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set())
  const [notice, setNotice] = useState<Notice | null>(null)
  const [revision, setRevision] = useState(0)
  const projectsRef = useRef<Project[]>([])

  const patch = useCallback((change: (data: Data) => Data) => {
    setState((current) => (current.kind === 'ready' ? { kind: 'ready', data: change(current.data) } : current))
  }, [])

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
      fetchFindings({ status: 'all' }, signal),
      fetchHealth(signal).catch(() => null),
    ])
      .then(([projects, months, findings, health]) =>
        setState({ kind: 'ready', data: { projects, months: months.months, asOf: months.as_of, findings: findings.items, health } }),
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

  const reload = useCallback(() => {
    setState({ kind: 'loading' })
    setAttempt((n) => n + 1)
  }, [])

  const reloadFindings = useCallback(async () => {
    try {
      const [findings, health] = await Promise.all([fetchFindings({ status: 'all' }), fetchHealth().catch(() => null)])
      patch((data) => ({ ...data, findings: findings.items, health }))
    } catch (error) {
      handleFailure(error)
    }
  }, [patch, handleFailure])

  const reloadProjects = useCallback(async () => {
    const fresh = await fetchAllProjects()
    patch((data) => ({ ...data, projects: fresh }))
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
            p.id === projectId ? { ...p, total_revenue: result.project_total, monthly: { ...p.monthly, [month]: result.amount } } : p,
          ),
        }))
        setRevision((n) => n + 1)
        setNotice({
          kind: 'success',
          text: result.changed ? `Saved ${label} for ${project?.name ?? 'the project'}.` : `${label} was already that value; nothing changed.`,
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
            await reloadProjects().catch(() => undefined)
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
    [patch, reloadProjects, onSessionEnded],
  )

  const currentMonth = currentMonthOf(state.kind === 'ready' ? state.data.asOf : null)
  const rows = useMemo(() => {
    if (state.kind !== 'ready') return []
    const { projects: list, findings, health } = state.data
    return list.map((p) => buildRow(p, findings, health, currentMonth)).sort(byPriority)
  }, [state, currentMonth])

  const value = useMemo<Workspace>(
    () => ({ state, currentMonth, rows, saveAmount, pending, revision, notice, setNotice, reload, reloadFindings }),
    [state, currentMonth, rows, saveAmount, pending, revision, notice, reload, reloadFindings],
  )
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
