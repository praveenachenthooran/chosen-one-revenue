import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  fetchConfirmations,
  fetchDeals,
  fetchFilterOptions,
  fetchForecast,
  fetchSyncStatus,
  triggerSync,
  type AuthUser,
  type Confirmation,
  type DealPage,
  type FilterOptions,
  type ForecastFilters,
  type ForecastTotals,
  type SyncStatus,
} from '../api'
import { formatMoney, formatMonthLabel } from '../format'
import ExportLinks from '../reporting/ExportLinks'
import DealPanel from './DealPanel'
import ForecastChart from './ForecastChart'
import { FILTERS } from './filters'
import { STATUS_LABELS } from './labels'
import StagesPanel from './StagesPanel'
import SyncBanner from './SyncBanner'

type Data = { forecast: ForecastTotals; deals: DealPage }
type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; data: Data }
type Notice = { ok: boolean; text: string }

const DEAL_VIEWS = [
  ['counting', 'In the forecast'],
  ['awaiting_confirmation', 'Awaiting confirmation'],
  ['linked', 'Committed (linked)'],
  ['closed_lost', 'Closed Lost'],
  ['excluded', 'Not in forecast'],
  ['', 'All deals'],
] as const

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

type Props = {
  user: AuthUser
  onSessionEnded: () => void
  onOpenProject: (projectId: number) => void
}

export default function PipelineView({ user, onSessionEnded, onOpenProject }: Props) {
  const [filters, setFilters] = useState<ForecastFilters>({})
  const [search, setSearch] = useState('')
  const [dealView, setDealView] = useState<string>('counting')
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const [statusError, setStatusError] = useState(false)
  const [confirmations, setConfirmations] = useState<Confirmation[]>([])
  const [options, setOptions] = useState<FilterOptions | null>(null)
  const [selected, setSelected] = useState<{ id: number; title: string } | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [chartOpen, setChartOpen] = useState(false)

  const reload = useCallback(() => {
    setState((current) => (current.kind === 'error' ? { kind: 'loading' } : current))
    setAttempt((n) => n + 1)
  }, [])
  const failure = useCallback(
    (error: unknown) => {
      if (isAbort(error)) return
      if (error instanceof ApiError && error.status === 401) onSessionEnded()
    },
    [onSessionEnded],
  )

  // Search is applied after a short pause in typing.
  useEffect(() => {
    const timer = setTimeout(() => setFilters((f) => (f.q === (search || undefined) ? f : { ...f, q: search || undefined })), 300)
    return () => clearTimeout(timer)
  }, [search])

  // Committed + weighted totals and the deal list: the heart of the page.
  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    Promise.all([fetchForecast(filters, signal), fetchDeals(filters, { status: dealView || undefined }, signal)])
      .then(([forecast, deals]) => setState({ kind: 'ready', data: { forecast, deals } }))
      .catch((error: unknown) => {
        if (isAbort(error)) return
        if (error instanceof ApiError && error.status === 401) return onSessionEnded()
        setState({ kind: 'error' })
      })
    return () => controller.abort()
  }, [filters, dealView, attempt, onSessionEnded])

  // Sync status, confirmation requests and filter options load independently, so a problem with
  // any one of them never hides the forecast.
  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    fetchSyncStatus(signal)
      .then((s) => {
        setStatus(s)
        setStatusError(false)
      })
      .catch((error: unknown) => {
        failure(error)
        if (!isAbort(error)) setStatusError(true)
      })
    fetchConfirmations(signal).then(setConfirmations).catch(failure)
    fetchFilterOptions(signal).then(setOptions).catch(failure)
    return () => controller.abort()
  }, [attempt, failure])

  async function syncNow() {
    setSyncing(true)
    setNotice(null)
    try {
      const run = await triggerSync()
      setNotice({
        ok: true,
        text: `Sync complete: ${run.deals_seen} deals (${run.deals_created} new, ${run.deals_updated} updated), ${run.drafts_created} draft project(s).`,
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return onSessionEnded()
      setNotice({ ok: false, text: error instanceof ApiError ? error.message : 'The sync could not be started.' })
    } finally {
      setSyncing(false)
      reload()
    }
  }

  const filtered = Object.values(filters).some(Boolean)
  const isAdmin = user.roles.includes('Administrator')

  return (
    <div className="pipeline-layout">
      <div className="pipeline-view">
      <SyncBanner status={status} statusError={statusError} syncing={syncing} onSync={() => void syncNow()} />
      {notice && (
        <div className={`notice notice-${notice.ok ? 'success' : 'error'}`} role={notice.ok ? 'status' : 'alert'}>
          <span>{notice.text}</span>
          <button type="button" className="btn-close btn-sm" aria-label="Dismiss message" onClick={() => setNotice(null)} />
        </div>
      )}

      {confirmations.length > 0 && (
        <section className="awaiting" aria-label="Awaiting confirmation">
          <h2 className="h6 mb-2">Awaiting confirmation ({confirmations.length})</h2>
          <ul className="list-unstyled mb-0">
            {confirmations.map((c) => (
              <li key={c.project_id} className="d-flex flex-wrap align-items-center gap-2">
                <strong>{c.customer}</strong> · {c.project_name} · {formatMoney(c.amount)}
                {c.overdue && <span className="overdue-badge">Overdue</span>}
                <button
                  type="button"
                  className="btn btn-link btn-sm p-0"
                  onClick={() => setSelected({ id: c.deal_id, title: c.project_name })}
                >
                  Review and confirm
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="filters" role="group" aria-label="Forecast filters">
        {FILTERS.map(({ key, label, options: pick }) => (
          <select
            key={key}
            className="form-select form-select-sm"
            aria-label={label}
            value={filters[key] ?? ''}
            onChange={(event) => setFilters((f) => ({ ...f, [key]: event.target.value || undefined }))}
          >
            <option value="">{label}: all</option>
            {(options ? pick(options) : []).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        ))}
        <input
          type="search"
          className="form-control form-control-sm"
          placeholder="Search deals"
          aria-label="Search deals"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        {filtered && (
          <button
            type="button"
            className="btn btn-link btn-sm"
            onClick={() => {
              setFilters({})
              setSearch('')
            }}
          >
            Clear filters
          </button>
        )}
      </div>
      <ExportLinks snapshotId={null} filters={filters} />

      {state.kind === 'loading' && (
        <div className="state-box" role="status" aria-live="polite">
          <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
          <span>Loading the pipeline…</span>
        </div>
      )}
      {state.kind === 'error' && (
        <div className="state-box" role="alert">
          <p className="mb-2">The pipeline could not be loaded.</p>
          <p className="text-secondary small mb-3">The committed forecast is unaffected.</p>
          <button type="button" className="btn btn-primary btn-sm" onClick={reload}>
            Try again
          </button>
        </div>
      )}

      {state.kind === 'ready' && (
        <>
          <section className="summary-cards" aria-label="Forecast totals">
            <div className="summary-card">
              <span className="summary-label">Committed</span>
              <strong>{formatMoney(state.data.forecast.committed_total)}</strong>
            </div>
            <div className="summary-card">
              <span className="summary-label">Weighted pipeline</span>
              <strong>{formatMoney(state.data.forecast.weighted_total)}</strong>
              <span className="text-secondary small">{state.data.forecast.weighted_deals} deals</span>
            </div>
            <div className="summary-card summary-total">
              <span className="summary-label">Combined</span>
              <strong>{formatMoney(state.data.forecast.combined_total)}</strong>
            </div>
          </section>
          {state.data.forecast.unphased.deals > 0 && (
            <p className="small text-secondary mb-0">
              {state.data.forecast.unphased.deals} deal(s) worth {formatMoney(state.data.forecast.unphased.weighted_amount)} weighted
              have no start date or duration, so they are not in the monthly figures.
            </p>
          )}
          {Number(state.data.forecast.outside_window_weighted) > 0 && (
            <p className="small text-secondary mb-0">
              {formatMoney(state.data.forecast.outside_window_weighted)} of weighted pipeline falls outside the months shown.
            </p>
          )}

          <div className="table-responsive monthly-table">
            <table className="table table-sm mb-0" aria-label="Monthly totals">
              <thead>
                <tr>
                  <th scope="col">&nbsp;</th>
                  {state.data.forecast.months.map((m) => (
                    <th key={m.month} scope="col" className="text-end">
                      {formatMonthLabel(m.month)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    ['Committed', 'committed'],
                    ['Weighted pipeline', 'weighted'],
                    ['Combined', 'total'],
                  ] as const
                ).map(([label, field]) => (
                  <tr key={field} className={field === 'total' ? 'fw-semibold' : undefined}>
                    <th scope="row">{label}</th>
                    {state.data.forecast.months.map((m) => (
                      <td key={m.month} className="text-end">
                        {formatMoney(m[field])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <details className="chart-details" onToggle={(event) => setChartOpen(event.currentTarget.open)}>
            <summary>Chart: committed, weighted and combined by month</summary>
            {chartOpen && <ForecastChart months={state.data.forecast.months} />}
          </details>

          <div className="d-flex flex-wrap align-items-center gap-2 mt-3">
            <h2 className="h6 mb-0">Deals</h2>
            <select
              className="form-select form-select-sm deal-view-select"
              aria-label="Deal view"
              value={dealView}
              onChange={(event) => setDealView(event.target.value)}
            >
              {DEAL_VIEWS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <span className="text-secondary small">{state.data.deals.total_items} shown</span>
          </div>

          {state.data.deals.items.length === 0 ? (
            <div className="state-box">
              <p className="mb-1">No deals match.</p>
              <p className="text-secondary small mb-0">
                {filtered ? 'Try clearing the filters.' : 'Run a HubSpot sync to bring deals in.'}
              </p>
            </div>
          ) : (
            <div className="table-responsive deals-table">
              <table className="table table-sm table-hover align-middle" aria-label="Deals">
                <thead>
                  <tr>
                    <th scope="col">Deal</th>
                    <th scope="col">Owner</th>
                    <th scope="col">Stage</th>
                    <th scope="col" className="text-end">
                      Amount
                    </th>
                    <th scope="col" className="text-end">
                      Weighted
                    </th>
                    <th scope="col">Starts</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="text-end">
                      Notes
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.deals.items.map((deal) => (
                    <tr key={deal.id}>
                      <td>
                        <button
                          type="button"
                          className="project-link"
                          onClick={() => setSelected({ id: deal.id, title: deal.name })}
                        >
                          {deal.name}
                        </button>
                        <div className="small text-secondary">{deal.company ?? `Company ${deal.company_id ?? 'unknown'}`}</div>
                      </td>
                      <td>{deal.owner_name ?? '—'}</td>
                      <td>
                        {deal.stage}
                        {deal.probability ? (
                          <span className="text-secondary small"> {Math.round(Number(deal.probability) * 100)}%</span>
                        ) : null}
                      </td>
                      <td className="text-end">{formatMoney(deal.amount)}</td>
                      <td className="text-end">{formatMoney(deal.weighted_amount)}</td>
                      <td>{deal.expected_start ? formatMonthLabel(deal.expected_start.slice(0, 7) + '-01') : '—'}</td>
                      <td>
                        <span className={`deal-status deal-${deal.status}`}>{STATUS_LABELS[deal.status]}</span>
                        {deal.draft?.overdue && !deal.draft.confirmed_at && <span className="overdue-badge">Overdue</span>}
                        {!deal.phaseable && deal.counts_in_forecast && <span className="unphased-badge">Not phased</span>}
                      </td>
                      <td className="text-end small">{deal.findings > 0 ? `${deal.findings} finding(s)` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <StagesPanel isAdmin={isAdmin} onChanged={reload} onSessionEnded={onSessionEnded} />
      </div>

      {selected && (
        <DealPanel
          key={selected.id}
          dealId={selected.id}
          title={selected.title}
          categories={options?.categories ?? []}
          onClose={() => setSelected(null)}
          onOpenProject={onOpenProject}
          onConfirmed={reload}
          onSessionEnded={onSessionEnded}
        />
      )}
    </div>
  )
}
