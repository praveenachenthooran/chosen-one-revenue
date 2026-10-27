import { useCallback, useEffect, useState } from 'react'
import { ApiError, confirmDraft, fetchDeal, type DealDetail } from '../api'
import { formatMoney, formatMoneyExact, formatMonthLabel } from '../format'
import { EVENT_LABELS, STATUS_HELP, STATUS_LABELS } from './labels'

type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; deal: DealDetail }

type Props = {
  dealId: number
  title: string
  categories: string[]
  onClose: () => void
  onOpenProject: (projectId: number) => void
  /** Called after a draft is confirmed, so totals and lists are re-read from the server. */
  onConfirmed: () => void
  onSessionEnded: () => void
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString() : '—'
}

export default function DealPanel({ dealId, title, categories, onClose, onOpenProject, onConfirmed, onSessionEnded }: Props) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [category, setCategory] = useState('')
  const [industry, setIndustry] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const load = useCallback(
    (signal?: AbortSignal) =>
      fetchDeal(dealId, signal)
        .then((deal) => setState({ kind: 'ready', deal }))
        .catch((failure: unknown) => {
          if (failure instanceof DOMException && failure.name === 'AbortError') return
          if (failure instanceof ApiError && failure.status === 401) return onSessionEnded()
          setState({ kind: 'error' })
        }),
    [dealId, onSessionEnded],
  )

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  async function confirm(deal: DealDetail) {
    if (!deal.draft) return
    setSubmitting(true)
    setError(null)
    try {
      await confirmDraft(deal.draft.project_id, {
        acknowledge_possible_duplicate: acknowledged,
        category: category || undefined,
        industry: industry.trim() || undefined,
      })
      setDone('Confirmed. The project is now committed and the weighted line has been retired.')
      onConfirmed()
      await load()
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) return onSessionEnded()
      setError(failure instanceof ApiError ? failure.message : 'Could not confirm. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  const deal = state.kind === 'ready' ? state.deal : null
  const draft = deal?.draft ?? null
  const duplicates = draft?.possible_duplicates ?? []
  const needsAck = duplicates.length > 0

  return (
    <aside className="detail-panel deal-panel" aria-label={`Details for deal ${title}`}>
      <div className="d-flex justify-content-between align-items-start mb-3">
        <div>
          <p className="eyebrow mb-1">{deal?.company ?? 'HubSpot deal'}</p>
          <h2 className="h5 mb-0">{title}</h2>
        </div>
        <button type="button" className="btn-close" aria-label="Close deal details" onClick={onClose} />
      </div>
      {state.kind === 'loading' && <p className="text-secondary">Loading…</p>}
      {state.kind === 'error' && <p className="text-danger">Could not load this deal.</p>}
      {deal && (
        <>
          <p className="mb-2">
            <span className={`deal-status deal-${deal.status}`}>{STATUS_LABELS[deal.status]}</span>
          </p>
          <p className="text-secondary small">{STATUS_HELP[deal.status]}</p>

          <dl className="detail-list">
            <dt>Stage</dt>
            <dd>
              {deal.stage}
              {deal.probability ? ` (${Math.round(Number(deal.probability) * 100)}%)` : ''}
            </dd>
            <dt>HubSpot amount</dt>
            <dd>{formatMoneyExact(deal.amount)}</dd>
            <dt>Weighted</dt>
            <dd>{formatMoneyExact(deal.weighted_amount)}</dd>
            <dt>Starts</dt>
            <dd>{formatDate(deal.expected_start)}</dd>
            <dt>Duration</dt>
            <dd>{deal.duration_months ? `${deal.duration_months} months` : '—'}</dd>
            <dt>Close date</dt>
            <dd>{formatDate(deal.close_date)}</dd>
            <dt>Owner</dt>
            <dd>{deal.owner_name ?? '—'}</dd>
            <dt>TECH · Region</dt>
            <dd>
              {deal.practice ?? '—'} · {deal.region ?? '—'}
            </dd>
            <dt>HubSpot ID</dt>
            <dd>{deal.hubspot_deal_id}</dd>
          </dl>

          {!deal.phaseable && (
            <div className="alert alert-warning py-2 small" role="note">
              Not phased by month: this deal has no {deal.unphased_reason === 'missing_amount' ? 'amount' : 'expected start or duration'}.
              Its weighted amount is shown as unphased rather than guessed.
            </div>
          )}

          {deal.monthly.length > 0 && (
            <>
              <h3 className="h6">Weighted by month</h3>
              <table className="table table-sm month-table">
                <tbody>
                  {deal.monthly.map((m) => (
                    <tr key={m.month}>
                      <td>{formatMonthLabel(m.month)}</td>
                      <td className="text-end">{formatMoney(m.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {Number(deal.outside_window) > 0 && (
                <p className="small text-secondary">
                  {formatMoney(deal.outside_window)} falls outside the forecast months shown.
                </p>
              )}
            </>
          )}

          {draft && (
            <section className="draft-box" aria-label="Draft project">
              <h3 className="h6">Draft project</h3>
              <p className="small mb-1">
                {draft.confirmed_at
                  ? `Confirmed ${formatDate(draft.confirmed_at)}.`
                  : `Owner ${draft.owner_name ?? 'not assigned'} · confirmation due ${formatDate(draft.due_at)}`}
                {draft.overdue && !draft.confirmed_at && <span className="overdue-badge">Overdue</span>}
              </p>
              {deal.schedule_check && (
                <p className="small mb-1">
                  Schedule {formatMoney(deal.schedule_check.schedule_total)} vs deal {formatMoney(deal.schedule_check.deal_amount)}
                  {deal.schedule_check.exceeds_threshold && (
                    <span className="text-danger"> · differs from the deal by more than 10%</span>
                  )}
                </p>
              )}
              {draft.project_id && (
                <button type="button" className="btn btn-link btn-sm p-0" onClick={() => onOpenProject(draft.project_id)}>
                  Open the project to edit its monthly schedule
                </button>
              )}

              {deal.can_confirm && (
                <form
                  className="confirm-form"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void confirm(deal)
                  }}
                >
                  <div className="row g-2 mt-1">
                    <div className="col-6">
                      <label className="form-label small mb-1" htmlFor="confirm-category">
                        Category
                      </label>
                      <select
                        id="confirm-category"
                        className="form-select form-select-sm"
                        value={category}
                        onChange={(event) => setCategory(event.target.value)}
                      >
                        <option value="">Not set</option>
                        {categories.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="col-6">
                      <label className="form-label small mb-1" htmlFor="confirm-industry">
                        Industry
                      </label>
                      <input
                        id="confirm-industry"
                        className="form-control form-control-sm"
                        value={industry}
                        onChange={(event) => setIndustry(event.target.value)}
                      />
                    </div>
                  </div>
                  {needsAck && (
                    <div className="alert alert-warning py-2 small mt-2 mb-2" role="note">
                      This customer already has committed project(s):{' '}
                      <strong>{duplicates.map((d) => d.name).join(', ')}</strong>.
                      <div className="form-check mt-1">
                        <input
                          id="ack-duplicate"
                          type="checkbox"
                          className="form-check-input"
                          checked={acknowledged}
                          onChange={(event) => setAcknowledged(event.target.checked)}
                        />
                        <label className="form-check-label" htmlFor="ack-duplicate">
                          This is new work, not a duplicate
                        </label>
                      </div>
                    </div>
                  )}
                  <button
                    type="submit"
                    className="btn btn-primary btn-sm mt-2"
                    disabled={submitting || (needsAck && !acknowledged)}
                  >
                    {submitting ? 'Confirming…' : 'Confirm schedule and commit'}
                  </button>
                </form>
              )}
              {!deal.can_confirm && !draft.confirmed_at && (
                <p className="small text-secondary mt-2 mb-0">
                  Only this project’s owners, Sales leadership or an Administrator can confirm it.
                </p>
              )}
              {error && (
                <div className="alert alert-danger py-2 small mt-2 mb-0" role="alert">
                  {error}
                </div>
              )}
              {done && (
                <div className="alert alert-success py-2 small mt-2 mb-0" role="status">
                  {done}
                </div>
              )}
            </section>
          )}

          {deal.findings_list.length > 0 && (
            <section aria-label="Findings" className="mt-3">
              <h3 className="h6">Findings</h3>
              <ul className="findings-list">
                {deal.findings_list.map((f) => (
                  <li key={f.code}>{f.message}</li>
                ))}
              </ul>
            </section>
          )}

          {deal.events.length > 0 && (
            <section aria-label="Deal history" className="mt-3">
              <h3 className="h6">History</h3>
              <ol className="history-list">
                {deal.events.map((e) => (
                  <li key={e.id}>
                    <div className="d-flex justify-content-between gap-2">
                      <strong>{EVENT_LABELS[e.event_type] ?? e.event_type}</strong>
                      <time dateTime={e.created_at} className="text-secondary">
                        {new Date(e.created_at).toLocaleString()}
                      </time>
                    </div>
                    <div>{e.message}</div>
                    <div className="text-secondary">{e.actor_label}</div>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      )}
    </aside>
  )
}
