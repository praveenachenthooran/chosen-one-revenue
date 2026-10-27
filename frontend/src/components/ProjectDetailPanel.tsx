import { useEffect, useState } from 'react'
import {
  fetchHistory,
  fetchProject,
  type HistoryItem,
  type MonthColumn,
  type Project,
  type ProjectDetail,
} from '../api'
import type { SaveAmount } from '../editing'
import { formatMoney, formatMoneyExact, formatMonthLabel } from '../format'

type DetailState = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; detail: ProjectDetail }
type HistoryState =
  | { kind: 'hidden' }
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'ready'; items: HistoryItem[] }
type RowStatus = { kind: 'saving' | 'saved' | 'error'; text?: string }

type Props = {
  project: Project
  months: MonthColumn[]
  saveAmount: SaveAmount
  /** Changes whenever any edit is saved, so the detail and history refetch. */
  refreshToken: number
  onClose: () => void
}

function omit<T>(record: Record<string, T>, key: string): Record<string, T> {
  return Object.fromEntries(Object.entries(record).filter(([k]) => k !== key))
}

function describeChange(item: HistoryItem): string {
  if (item.field === 'revenue' && item.month) return formatMonthLabel(item.month)
  if (item.field === 'baseline' && item.month) return `${formatMonthLabel(item.month)} (workbook value)`
  if (item.field === 'project' && item.change_type === 'create') return 'Imported from workbook'
  return item.field.replace('owner:', 'Owner: ').replace('_', ' ')
}

function HistorySection({ state }: { state: HistoryState }) {
  return (
    <section aria-labelledby="history-title" className="mt-4">
      <h3 id="history-title" className="h6">
        Change history
      </h3>
      {state.kind === 'hidden' && (
        <p className="text-secondary small">
          Change history is visible to this project&apos;s owners, Finance, Sales leadership and
          Administrators.
        </p>
      )}
      {state.kind === 'loading' && <p className="text-secondary small">Loading history…</p>}
      {state.kind === 'error' && <p className="text-danger small">Could not load the history.</p>}
      {state.kind === 'ready' && state.items.length === 0 && (
        <p className="text-secondary small">No changes recorded yet.</p>
      )}
      {state.kind === 'ready' && state.items.length > 0 && (
        <ol className="history-list">
          {state.items.map((item) => (
            <li key={item.id}>
              <div className="d-flex justify-content-between gap-2">
                <strong>{describeChange(item)}</strong>
                <time dateTime={item.created_at} className="text-secondary">
                  {new Date(item.created_at).toLocaleString()}
                </time>
              </div>
              {item.field === 'revenue' || item.field === 'baseline' ? (
                <div>
                  {formatMoneyExact(item.old_value)} → {formatMoneyExact(item.new_value)}
                </div>
              ) : null}
              <div className="text-secondary">
                {item.from_import ? 'Excel import' : item.actor_name}
                {item.reason ? ` · “${item.reason}”` : ''}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

export default function ProjectDetailPanel({ project, months, saveAmount, refreshToken, onClose }: Props) {
  const [detailState, setDetailState] = useState<DetailState>({ kind: 'loading' })
  const [history, setHistory] = useState<HistoryState>({ kind: 'loading' })
  const [reason, setReason] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [status, setStatus] = useState<Record<string, RowStatus>>({})

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    fetchProject(project.id, signal)
      .then((detail) => {
        setDetailState({ kind: 'ready', detail })
        if (!detail.can_view_history) {
          setHistory({ kind: 'hidden' })
          return
        }
        return fetchHistory(project.id, signal).then((page) =>
          setHistory({ kind: 'ready', items: page.items }),
        )
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setDetailState((current) => (current.kind === 'ready' ? current : { kind: 'error' }))
        setHistory((current) => (current.kind === 'ready' ? current : { kind: 'error' }))
      })
    return () => controller.abort()
  }, [project.id, refreshToken])

  async function commit(month: string, current: string) {
    const draft = drafts[month]
    if (draft === undefined || draft === current) return
    setStatus((s) => ({ ...s, [month]: { kind: 'saving' } }))
    const outcome = await saveAmount(project.id, month, draft, reason.trim() || undefined)
    if (outcome.ok) {
      setDrafts((d) => omit(d, month))
      setStatus((s) => ({ ...s, [month]: { kind: 'saved' } }))
    } else {
      setStatus((s) => ({ ...s, [month]: { kind: 'error', text: outcome.message } }))
    }
  }

  const detail = detailState.kind === 'ready' ? detailState.detail : null
  const byMonth = new Map(detail?.monthly_revenue.map((m) => [m.month, m]))

  return (
    <aside className="detail-panel" aria-label={`Details for ${project.name}`}>
      <div className="d-flex justify-content-between align-items-start mb-3">
        <div>
          <p className="eyebrow mb-1">{project.customer}</p>
          <h2 className="h5 mb-0">{project.name}</h2>
        </div>
        <button type="button" className="btn-close" aria-label="Close details" onClick={onClose} />
      </div>
      {detailState.kind === 'loading' && <p className="text-secondary">Loading…</p>}
      {detailState.kind === 'error' && <p className="text-danger">Could not load this project.</p>}
      {detail && (
        <>
          {detail.attention.length > 0 && (
            <div className="alert alert-warning py-2 small" role="note">
              <strong>Needs attention</strong>
              <ul className="mb-0 ps-3">
                {detail.attention.map((a) => (
                  <li key={a.code}>{a.message}</li>
                ))}
              </ul>
            </div>
          )}
          <dl className="detail-list">
            <dt>Status</dt>
            <dd>{detail.status}</dd>
            {detail.owners.flatMap((o) => [
              <dt key={`${o.role}-t`}>{o.role.replace('_', ' ')}</dt>,
              <dd key={`${o.role}-d`}>{o.name}</dd>,
            ])}
            <dt>Current total</dt>
            <dd>{formatMoney(project.total_revenue)}</dd>
            <dt>Source</dt>
            <dd>
              {detail.source_row_id}
              {detail.source_row_number ? ` (row ${detail.source_row_number})` : ''}
            </dd>
          </dl>

          <p className={`small ${detail.can_edit ? 'text-success' : 'text-secondary'}`}>
            {detail.can_edit
              ? 'You can edit this project’s monthly forecast.'
              : 'Read-only: you are not an owner of this project.'}
          </p>
          {detail.can_edit && (
            <label className="form-label small w-100">
              Reason for changes (optional)
              <input
                className="form-control form-control-sm"
                maxLength={500}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
          )}

          <table className="table table-sm month-table">
            <thead>
              <tr>
                <th>Month</th>
                <th className="text-end">Forecast</th>
                <th className="text-end">Imported</th>
              </tr>
            </thead>
            <tbody>
              {months.map((m) => {
                const row = byMonth.get(m.month)
                const current = row?.amount ?? ''
                const rowStatus = status[m.month]
                return (
                  <tr key={m.month}>
                    <td>
                      {m.label} <span className="text-secondary small">{m.fiscal_year}</span>
                    </td>
                    <td className="text-end">
                      {detail.can_edit ? (
                        <input
                          className={`form-control form-control-sm text-end amount-input${
                            rowStatus?.kind === 'error' ? ' is-invalid' : ''
                          }`}
                          inputMode="decimal"
                          aria-label={`Revenue for ${m.label}`}
                          value={drafts[m.month] ?? (row ? row.amount : '')}
                          onChange={(event) =>
                            setDrafts((d) => ({ ...d, [m.month]: event.target.value }))
                          }
                          onBlur={() => void commit(m.month, current)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') event.currentTarget.blur()
                            if (event.key === 'Escape') {
                              setDrafts((d) => omit(d, m.month))
                              setStatus((s) => ({ ...s, [m.month]: { kind: 'saved' } }))
                            }
                          }}
                        />
                      ) : (
                        formatMoney(row?.amount)
                      )}
                      {rowStatus?.kind === 'saving' && (
                        <div className="small text-secondary">Saving…</div>
                      )}
                      {rowStatus?.kind === 'error' && (
                        <div className="small text-danger" role="alert">
                          {rowStatus.text}
                        </div>
                      )}
                    </td>
                    <td className="text-end small text-secondary">
                      {row?.baseline_amount === null && row ? 'added here' : formatMoney(row?.baseline_amount)}
                      {row?.edited && row.baseline_amount !== null ? ' · edited' : ''}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}
      {detail && <HistorySection state={history} />}
    </aside>
  )
}
