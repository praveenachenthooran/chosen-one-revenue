import { useCallback, useEffect, useState } from 'react'
import { ApiError, fetchFindings, fetchHealth, runChecks, type Finding, type FindingFilters, type Health } from '../api'
import { formatMoney } from '../format'
import FindingPanel from './FindingPanel'
import HealthSummary from './HealthSummary'
import { RULE_LABELS, STATUS_LABELS } from './labels'

const STATUSES = [
  ['open', 'Open'],
  ['accepted', 'Accepted'],
  ['dismissed', 'Dismissed'],
  ['resolved', 'Resolved (no longer detected)'],
  ['all', 'All'],
] as const

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

type Props = {
  onSessionEnded: () => void
  onOpenProject: (projectId: number) => void
  onOpenDeal: (deal: { id: number; title: string }) => void
}

/** The investigation workspace: health, findings, and (in a panel) evidence, AI investigation and review. */
export default function QualityView({ onSessionEnded, onOpenProject, onOpenDeal }: Props) {
  const [filters, setFilters] = useState<FindingFilters>({ status: 'open' })
  const [findings, setFindings] = useState<Finding[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [running, setRunning] = useState(false)
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [ownerFilter, setOwnerFilter] = useState<number | null | undefined>(undefined)

  const handle = useCallback(
    (error: unknown) => {
      if (isAbort(error)) return true
      if (error instanceof ApiError && error.status === 401) {
        onSessionEnded()
        return true
      }
      return false
    },
    [onSessionEnded],
  )
  const reload = useCallback(() => setAttempt((n) => n + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    fetchFindings(filters, controller.signal)
      .then((page) => {
        setFindings(page.items)
        setFailed(false)
      })
      .catch((error: unknown) => {
        if (!handle(error)) setFailed(true)
      })
    // The health summary loads on its own, so a problem with one never hides the other.
    fetchHealth(controller.signal).then(setHealth).catch(handle)
    return () => controller.abort()
  }, [filters, attempt, handle])

  async function run() {
    setRunning(true)
    setNotice(null)
    try {
      const result = await runChecks()
      setNotice({
        ok: true,
        text: `Checks complete: ${result.findings_new} new, ${result.findings_reopened} reopened, ${result.findings_resolved} resolved, ${result.findings_open} open.`,
      })
    } catch (error) {
      if (!handle(error)) setNotice({ ok: false, text: error instanceof ApiError ? error.message : 'The checks could not be run.' })
    } finally {
      setRunning(false)
      reload()
    }
  }

  const visible =
    findings === null
      ? null
      : ownerFilter === undefined
        ? findings
        : findings.filter((f) => f.routed_to_id === ownerFilter)

  return (
    <div className="pipeline-layout">
      <div className="pipeline-view">
        {notice && (
          <div className={`notice notice-${notice.ok ? 'success' : 'error'}`} role={notice.ok ? 'status' : 'alert'}>
            <span>{notice.text}</span>
            <button type="button" className="btn-close btn-sm" aria-label="Dismiss message" onClick={() => setNotice(null)} />
          </div>
        )}
        {health ? (
          <HealthSummary
            health={health}
            running={running}
            onRun={() => void run()}
            onFilterOwner={(id) => {
              setFilters((f) => ({ ...f, status: 'open' }))
              setOwnerFilter(id)
            }}
          />
        ) : (
          <p className="text-secondary small mb-0" role="status">
            Loading forecast health…
          </p>
        )}

        <div className="filters" role="group" aria-label="Finding filters">
          <select
            className="form-select form-select-sm"
            aria-label="Status"
            value={filters.status ?? 'open'}
            onChange={(event) => setFilters((f) => ({ ...f, status: event.target.value }))}
          >
            {STATUSES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            className="form-select form-select-sm"
            aria-label="Severity"
            value={filters.severity ?? ''}
            onChange={(event) => setFilters((f) => ({ ...f, severity: event.target.value || undefined }))}
          >
            <option value="">Severity: all</option>
            <option value="error">Errors</option>
            <option value="warning">Warnings</option>
          </select>
          <select
            className="form-select form-select-sm"
            aria-label="Check"
            value={filters.rule ?? ''}
            onChange={(event) => setFilters((f) => ({ ...f, rule: event.target.value || undefined }))}
          >
            <option value="">Check: all</option>
            {Object.entries(RULE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <label className="form-check d-flex align-items-center gap-1 mb-0">
            <input
              type="checkbox"
              className="form-check-input mt-0"
              checked={filters.mine === 'true'}
              onChange={(event) => setFilters((f) => ({ ...f, mine: event.target.checked ? 'true' : undefined }))}
            />
            <span className="small">Routed to me</span>
          </label>
          {ownerFilter !== undefined && (
            <button type="button" className="btn btn-link btn-sm" onClick={() => setOwnerFilter(undefined)}>
              Clear owner filter
            </button>
          )}
        </div>

        {failed && (
          <div className="state-box" role="alert">
            <p className="mb-2">Findings could not be loaded.</p>
            <p className="text-secondary small mb-3">The forecast itself is unaffected.</p>
            <button type="button" className="btn btn-primary btn-sm" onClick={reload}>
              Try again
            </button>
          </div>
        )}
        {!failed && visible === null && (
          <div className="state-box" role="status" aria-live="polite">
            <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
            <span>Loading findings…</span>
          </div>
        )}
        {visible !== null && visible.length === 0 && (
          <div className="state-box">
            <p className="mb-1">No findings match.</p>
            <p className="text-secondary small mb-0">
              {health?.last_run ? 'Everything the checks looked for is in order, or has been decided.' : 'Run the checks to look for problems.'}
            </p>
          </div>
        )}
        {visible !== null && visible.length > 0 && (
          <div className="table-responsive deals-table">
            <table className="table table-sm table-hover align-middle" aria-label="Findings">
              <thead>
                <tr>
                  <th scope="col">Finding</th>
                  <th scope="col">Check</th>
                  <th scope="col">Routed to</th>
                  <th scope="col" className="text-end">
                    Impact
                  </th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((f) => (
                  <tr key={f.id} className={f.id === selectedId ? 'table-active' : undefined}>
                    <td>
                      <span className={`sev sev-${f.severity}`}>{f.severity}</span>{' '}
                      <button type="button" className="project-link" onClick={() => setSelectedId(f.id)}>
                        {f.title}
                      </button>
                    </td>
                    <td className="small">{RULE_LABELS[f.rule_code] ?? f.rule_label}</td>
                    <td>{f.routed_to_name ?? <span className="text-secondary">Unassigned</span>}</td>
                    <td className="text-end">{formatMoney(f.financial_impact)}</td>
                    <td>
                      <span className={`finding-state state-${f.awaiting_review ? 'awaiting' : f.investigation_status === 'investigating' ? 'investigating' : f.status}`}>
                        {f.awaiting_review
                          ? 'Awaiting review'
                          : f.investigation_status === 'investigating' && f.status === 'open'
                            ? 'Investigating'
                            : STATUS_LABELS[f.status]}
                      </span>
                      {f.investigation_status === 'failed' && f.status === 'open' && <span className="unphased-badge">Investigation failed</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedId !== null && (
        <FindingPanel
          key={selectedId}
          findingId={selectedId}
          onClose={() => setSelectedId(null)}
          onChanged={reload}
          onSessionEnded={onSessionEnded}
          onOpenProject={onOpenProject}
          onOpenDeal={onOpenDeal}
        />
      )}
    </div>
  )
}
