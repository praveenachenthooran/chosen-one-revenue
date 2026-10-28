import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  fetchFilterOptions,
  fetchSnapshot,
  fetchSnapshots,
  takeSnapshot,
  verifySnapshot,
  type FilterOptions,
  type ForecastFilters,
  type SnapshotList,
  type SnapshotTotals,
} from '../api'
import { formatMoney, formatMonthLabel } from '../format'
import { FILTERS } from '../pipeline/filters'
import ExportLinks from './ExportLinks'

type Notice = { ok: boolean; text: string }

const when = (iso: string) => new Date(iso).toLocaleString()
const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

type Props = { onSessionEnded: () => void }

/** Immutable monthly snapshots: history, the stored totals (filterable), integrity check, export. */
export default function SnapshotsView({ onSessionEnded }: Props) {
  const [list, setList] = useState<SnapshotList | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [filters, setFilters] = useState<ForecastFilters>({})
  const [detail, setDetail] = useState<SnapshotTotals | null>(null)
  const [detailFailed, setDetailFailed] = useState(false)
  const [options, setOptions] = useState<FilterOptions | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [integrity, setIntegrity] = useState<{ id: number; intact: boolean } | null>(null)

  const sessionEnded = useCallback(
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

  useEffect(() => {
    const controller = new AbortController()
    fetchSnapshots(controller.signal)
      .then((result) => {
        setList(result)
        setFailed(false)
      })
      .catch((error: unknown) => {
        if (!sessionEnded(error)) setFailed(true)
      })
    fetchFilterOptions(controller.signal).then(setOptions).catch(sessionEnded)
    return () => controller.abort()
  }, [attempt, sessionEnded])

  useEffect(() => {
    if (selectedId === null) return
    const controller = new AbortController()
    fetchSnapshot(selectedId, filters, controller.signal)
      .then((result) => {
        setDetail(result)
        setDetailFailed(false)
      })
      .catch((error: unknown) => {
        if (!sessionEnded(error)) setDetailFailed(true)
      })
    return () => controller.abort()
  }, [selectedId, filters, sessionEnded])

  function open(id: number) {
    setSelectedId(id)
    setDetail(null)
    setDetailFailed(false)
    setIntegrity(null)
  }

  async function take() {
    setBusy(true)
    setNotice(null)
    try {
      const snapshot = await takeSnapshot(note)
      setNote('')
      setNotice({ ok: true, text: `Snapshot #${snapshot.id} taken. It can no longer be changed.` })
      setAttempt((n) => n + 1)
      open(snapshot.id)
    } catch (error) {
      if (sessionEnded(error)) return
      setNotice({ ok: false, text: error instanceof ApiError ? error.message : 'The snapshot could not be taken.' })
    } finally {
      setBusy(false)
    }
  }

  async function verify(id: number) {
    try {
      const result = await verifySnapshot(id)
      setIntegrity({ id, intact: result.intact })
    } catch (error) {
      if (!sessionEnded(error)) setNotice({ ok: false, text: 'The integrity check could not be run.' })
    }
  }

  if (failed) {
    return (
      <div className="state-box" role="alert">
        <p className="mb-2">Snapshots could not be loaded.</p>
        <p className="text-secondary small mb-3">The live forecast is unaffected.</p>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </div>
    )
  }
  if (!list) {
    return (
      <div className="state-box" role="status" aria-live="polite">
        <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
        <span>Loading snapshots…</span>
      </div>
    )
  }

  const filtered = Object.values(filters).some(Boolean)
  return (
    <div className="report-layout">
      <section className="report-section" aria-label="Snapshot schedule">
        <h2 className="h6">Forecast snapshots</h2>
        <p className="small text-secondary mb-2">
          A snapshot freezes the committed, weighted and combined forecast exactly as it stands. It is taken automatically on the
          first business day of each month and can never be edited or deleted; later edits are flagged as post-snapshot changes.
        </p>
        <p className="small mb-2">
          {list.scheduled_taken_this_month
            ? 'This month’s scheduled snapshot has been taken. '
            : 'This month’s scheduled snapshot has not been taken yet. '}
          Next scheduled: <strong>{day(list.next_scheduled)}</strong>.
          {list.last_job && (
            <span className={list.last_job.status === 'failed' ? 'text-danger' : 'text-secondary'}>
              {' '}
              Last job run {when(list.last_job.finished_at)}: {list.last_job.status}. {list.last_job.message}
            </span>
          )}
        </p>
        {list.can_take ? (
          <form
            className="d-flex flex-wrap gap-2 align-items-center"
            onSubmit={(event) => {
              event.preventDefault()
              void take()
            }}
          >
            <input
              className="form-control form-control-sm snapshot-note"
              aria-label="Snapshot note"
              placeholder="Note (optional), e.g. Board pack"
              maxLength={500}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
            <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
              {busy ? 'Taking snapshot…' : 'Take snapshot now'}
            </button>
          </form>
        ) : (
          <p className="small text-secondary mb-0">Finance and Administrators can take a snapshot on demand.</p>
        )}
        {notice && (
          <div className={`notice notice-${notice.ok ? 'success' : 'error'} mt-2`} role={notice.ok ? 'status' : 'alert'}>
            <span>{notice.text}</span>
            <button type="button" className="btn-close btn-sm" aria-label="Dismiss message" onClick={() => setNotice(null)} />
          </div>
        )}
      </section>

      {list.items.length === 0 ? (
        <div className="state-box">
          <p className="mb-1">No snapshots yet.</p>
          <p className="text-secondary small mb-0">The first is taken on the next first business day, or by Finance on demand.</p>
        </div>
      ) : (
        <div className="table-responsive report-section">
          <table className="table table-sm table-hover align-middle mb-0" aria-label="Snapshots">
            <thead>
              <tr>
                <th scope="col">Snapshot</th>
                <th scope="col">Taken</th>
                <th scope="col">By</th>
                <th scope="col" className="text-end">
                  Committed
                </th>
                <th scope="col" className="text-end">
                  Weighted
                </th>
                <th scope="col" className="text-end">
                  Combined
                </th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((s) => (
                <tr key={s.id} className={s.id === selectedId ? 'table-active' : undefined}>
                  <td>
                    <button type="button" className="project-link" onClick={() => open(s.id)}>
                      #{s.id} {s.label}
                    </button>
                    <span className={`snapshot-kind kind-${s.kind}`}>{s.kind === 'scheduled' ? 'Scheduled' : 'Manual'}</span>
                    {s.note && <div className="small text-secondary">{s.note}</div>}
                  </td>
                  <td>{when(s.taken_at)}</td>
                  <td>{s.taken_by_label}</td>
                  <td className="text-end">{formatMoney(s.committed_total)}</td>
                  <td className="text-end">{formatMoney(s.weighted_total)}</td>
                  <td className="text-end fw-semibold">{formatMoney(s.combined_total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedId !== null && (
        <section className="report-section" aria-label="Snapshot detail">
          {detailFailed && (
            <p className="text-danger mb-0" role="alert">
              This snapshot could not be loaded.
            </p>
          )}
          {!detail && !detailFailed && (
            <p className="text-secondary mb-0" role="status">
              Loading snapshot…
            </p>
          )}
          {detail && (
            <>
              <div className="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-2">
                <div>
                  <h3 className="h6 mb-1">
                    Snapshot #{detail.snapshot.id}: {detail.snapshot.label}
                  </h3>
                  <p className="small text-secondary mb-0">
                    Taken {when(detail.snapshot.taken_at)} by {detail.snapshot.taken_by_label}
                    {detail.snapshot.as_of ? ` · data as of ${detail.snapshot.as_of}` : ''} · {detail.snapshot.project_count} projects,{' '}
                    {detail.snapshot.deal_count} deals · checksum{' '}
                    <code title={detail.snapshot.checksum}>{detail.snapshot.checksum.slice(0, 12)}…</code>
                  </p>
                </div>
                <div className="d-flex align-items-center gap-2">
                  <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => void verify(detail.snapshot.id)}>
                    Verify integrity
                  </button>
                  {integrity?.id === detail.snapshot.id && (
                    <span className={integrity.intact ? 'text-success' : 'text-danger'} role="status">
                      {integrity.intact ? 'Intact: matches its checksum' : 'ALTERED: does not match its checksum'}
                    </span>
                  )}
                </div>
              </div>

              <div className="filters mb-2" role="group" aria-label="Snapshot filters">
                {FILTERS.map(({ key, label, options: pick }) => (
                  <select
                    key={key}
                    className="form-select form-select-sm"
                    aria-label={`Snapshot ${label}`}
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
                {filtered && (
                  <button type="button" className="btn btn-link btn-sm" onClick={() => setFilters({})}>
                    Clear filters
                  </button>
                )}
              </div>

              <div className="summary-cards mb-2" aria-label="Snapshot totals">
                <div className="summary-card">
                  <span className="summary-label">Committed</span>
                  <strong>{formatMoney(detail.committed_total)}</strong>
                </div>
                <div className="summary-card">
                  <span className="summary-label">Weighted pipeline</span>
                  <strong>{formatMoney(detail.weighted_total)}</strong>
                </div>
                <div className="summary-card summary-total">
                  <span className="summary-label">Combined</span>
                  <strong>{formatMoney(detail.combined_total)}</strong>
                </div>
              </div>
              {detail.filters_applied && (
                <p className="small text-secondary">
                  Filters apply to what was stored at the time. Deals that could not be phased are only reported for the unfiltered snapshot.
                </p>
              )}
              {!detail.filters_applied && detail.unphased && detail.unphased.deals > 0 && (
                <p className="small text-secondary">
                  {detail.unphased.deals} deal(s) worth {formatMoney(detail.unphased.weighted_amount)} weighted had no start date or
                  duration, so they are not in the monthly figures.
                </p>
              )}

              <div className="table-responsive monthly-table mb-2">
                <table className="table table-sm mb-0" aria-label="Snapshot monthly totals">
                  <thead>
                    <tr>
                      <th scope="col">&nbsp;</th>
                      {detail.months.map((m) => (
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
                        {detail.months.map((m) => (
                          <td key={m.month} className="text-end">
                            {formatMoney(m[field])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ExportLinks snapshotId={detail.snapshot.id} filters={filters} />
            </>
          )}
        </section>
      )}
    </div>
  )
}
