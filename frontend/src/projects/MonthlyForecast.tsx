import { useState } from 'react'
import type { MonthColumn, ProjectDetail } from '../api'
import type { SaveAmount } from '../editing'
import { formatMoney } from '../format'

type RowStatus = { kind: 'saving' | 'saved' | 'error'; text?: string }

const omit = <T,>(record: Record<string, T>, key: string): Record<string, T> =>
  Object.fromEntries(Object.entries(record).filter(([k]) => k !== key))

type Props = {
  detail: ProjectDetail
  months: MonthColumn[]
  currentMonth: string
  saveAmount: SaveAmount
}

/** Every month of the project's forecast. Owners can edit any month; the server enforces who may. */
export default function MonthlyForecast({ detail, months, currentMonth, saveAmount }: Props) {
  const [reason, setReason] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [status, setStatus] = useState<Record<string, RowStatus>>({})
  const byMonth = new Map(detail.monthly_revenue.map((m) => [m.month, m]))

  async function commit(month: string, current: string) {
    const draft = drafts[month]
    if (draft === undefined || draft === current) return
    setStatus((s) => ({ ...s, [month]: { kind: 'saving' } }))
    const outcome = await saveAmount(detail.id, month, draft, reason.trim() || undefined)
    if (outcome.ok) {
      setDrafts((d) => omit(d, month))
      setStatus((s) => ({ ...s, [month]: { kind: 'saved' } }))
    } else {
      setStatus((s) => ({ ...s, [month]: { kind: 'error', text: outcome.message } }))
    }
  }

  return (
    <div>
      {detail.can_edit ? (
        <div className="field mb-3" style={{ maxWidth: 420 }}>
          <label htmlFor="edit-reason">Reason for changes (optional)</label>
          <input id="edit-reason" className="input" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
      ) : (
        <p className="hint mb-3">You have view-only access to this project’s forecast.</p>
      )}
      <div className="tscroll" style={{ maxHeight: 440, overflowY: 'auto' }}>
        <table className="mgrid">
          <thead>
            <tr>
              <th>Month</th>
              <th className="r">Forecast</th>
              <th className="r">Original import</th>
            </tr>
          </thead>
          <tbody>
            {months.map((m) => {
              const row = byMonth.get(m.month)
              const current = row?.amount ?? ''
              const rowStatus = status[m.month]
              return (
                <tr key={m.month} className={`${m.is_closed ? 'closed' : ''} ${m.month === currentMonth ? 'current' : ''}`}>
                  <td>
                    {m.label} <small>{m.fiscal_year}</small>
                  </td>
                  <td className="r">
                    {detail.can_edit ? (
                      <input
                        className={`form-control amount-input${rowStatus?.kind === 'error' ? ' is-invalid' : ''}`}
                        inputMode="decimal"
                        aria-label={`Revenue for ${m.label}`}
                        value={drafts[m.month] ?? current}
                        onChange={(event) => setDrafts((d) => ({ ...d, [m.month]: event.target.value }))}
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
                      formatMoney(row?.amount) || '—'
                    )}
                    {rowStatus?.kind === 'saving' && <div className="hint">Saving…</div>}
                    {rowStatus?.kind === 'error' && (
                      <div className="hint text-danger" role="alert">
                        {rowStatus.text}
                      </div>
                    )}
                  </td>
                  <td className="r">
                    {row ? (row.baseline_amount === null ? <span className="pill brand">Added here</span> : formatMoney(row.baseline_amount)) : '—'}
                    {row?.edited && row.baseline_amount !== null ? <span className="pill amber ms-2">Edited</span> : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
