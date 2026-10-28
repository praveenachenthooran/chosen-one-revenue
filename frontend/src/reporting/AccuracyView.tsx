import { useCallback, useEffect, useState } from 'react'
import { ApiError, fetchAccuracy, type AccuracyLag, type AccuracyReport } from '../api'
import { formatMoney, formatMonthLabel } from '../format'

const LAGS = [3, 2, 1] as const

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

function signed(value: string | null): string {
  if (value === null) return '—'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function Cell({ lag, tolerance }: { lag: AccuracyLag | undefined; tolerance: string }) {
  if (!lag || lag.status === 'no_snapshot') {
    return (
      <>
        <td className="text-end text-secondary">No snapshot</td>
        <td />
      </>
    )
  }
  if (lag.status === 'month_not_in_snapshot') {
    return (
      <>
        <td className="text-end text-secondary">Not in snapshot</td>
        <td />
      </>
    )
  }
  return (
    <>
      <td className="text-end" title={lag.snapshot_label ?? undefined}>
        {formatMoney(lag.forecast)}
      </td>
      <td className={`text-end ${lag.flagged ? 'variance-flag' : 'variance-ok'}`}>
        {lag.variance_percent === null ? 'n/a (no actual)' : signed(lag.variance_percent)}
        {lag.flagged && <span className="flag-badge">Outside ±{Number(tolerance)}%</span>}
      </td>
    </>
  )
}

type Props = { onSessionEnded: () => void }

/** Forecast accuracy: the M-3, M-2 and M-1 snapshots against actuals; months outside the target are flagged. */
export default function AccuracyView({ onSessionEnded }: Props) {
  const [report, setReport] = useState<AccuracyReport | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

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
    fetchAccuracy(controller.signal)
      .then((r) => {
        setReport(r)
        setFailed(false)
      })
      .catch((error: unknown) => {
        if (!sessionEnded(error)) setFailed(true)
      })
    return () => controller.abort()
  }, [attempt, sessionEnded])

  if (failed) {
    return (
      <div className="state-box" role="alert">
        <p className="mb-2">The accuracy report could not be loaded.</p>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </div>
    )
  }
  if (!report) {
    return (
      <div className="state-box" role="status" aria-live="polite">
        <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
        <span>Loading the accuracy report…</span>
      </div>
    )
  }

  const target = report.totals.find((t) => t.lag === report.target_lag)
  return (
    <div className="report-layout">
      <section className="report-section" aria-label="Accuracy summary">
        <h2 className="h6">Forecast accuracy</h2>
        <p className="small text-secondary mb-2">
          For each month with actuals, the forecast (committed plus weighted) from the snapshot taken one, two and three months earlier
          is compared with what was actually earned. Variance is forecast minus actual, as a percentage of the actual. The target is
          within ±{Number(report.tolerance_percent)}% at a three-month look-ahead; months outside it are flagged.
        </p>
        {target && target.months_compared > 0 ? (
          <p className="mb-0" role="status">
            <strong>
              M-{report.target_lag}: {target.months_compared - target.months_flagged} of {target.months_compared} month(s) within target
            </strong>
            {' · '}in total {signed(target.variance_percent)}
            {target.flagged ? ' (outside the target)' : ' (within the target)'}
          </p>
        ) : (
          <p className="mb-0 text-secondary" role="status">
            No month has both actuals and an M-{report.target_lag} snapshot yet.
          </p>
        )}
      </section>

      {report.months.length === 0 ? (
        <div className="state-box">
          <p className="mb-1">No actuals have been loaded.</p>
          <p className="text-secondary small mb-0">Load monthly actuals on the Actuals tab to see accuracy.</p>
        </div>
      ) : (
        <div className="table-responsive report-section">
          <table className="table table-sm align-middle mb-0" aria-label="Accuracy by month">
            <thead>
              <tr>
                <th scope="col" rowSpan={2}>
                  Month
                </th>
                <th scope="col" rowSpan={2} className="text-end">
                  Actual
                </th>
                {LAGS.map((lag) => (
                  <th key={lag} scope="colgroup" colSpan={2} className="text-center">
                    M-{lag} snapshot{lag === report.target_lag ? ' (target)' : ''}
                  </th>
                ))}
              </tr>
              <tr>
                {LAGS.map((lag) => (
                  <th key={lag} scope="col" colSpan={2} className="text-center small text-secondary">
                    Forecast · variance
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.months.map((m) => (
                <tr key={m.month}>
                  <th scope="row">{formatMonthLabel(m.month)}</th>
                  <td className="text-end">{formatMoney(m.actual)}</td>
                  {LAGS.map((lag) => (
                    <Cell key={lag} lag={m.lags.find((l) => l.lag === lag)} tolerance={report.tolerance_percent} />
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="fw-semibold">
                <th scope="row">Total</th>
                <td />
                {LAGS.map((lag) => {
                  const total = report.totals.find((t) => t.lag === lag)
                  if (!total || total.months_compared === 0) {
                    return (
                      <td key={lag} colSpan={2} className="text-end text-secondary">
                        —
                      </td>
                    )
                  }
                  return (
                    <td key={lag} colSpan={2} className={`text-end ${total.flagged ? 'variance-flag' : 'variance-ok'}`}>
                      {formatMoney(total.forecast)} vs {formatMoney(total.actual)}: {signed(total.variance_percent)}
                      <span className="small text-secondary"> ({total.months_compared} mo.)</span>
                      {total.flagged && <span className="flag-badge">Outside ±{Number(report.tolerance_percent)}%</span>}
                    </td>
                  )
                })}
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      <p className="small text-secondary mb-0">
        A look-ahead with no snapshot is shown as such and left out of the totals; nothing is estimated.
      </p>
    </div>
  )
}
