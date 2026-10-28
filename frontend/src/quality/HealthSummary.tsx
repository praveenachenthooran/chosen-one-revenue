import type { Health } from '../api'
import { formatMoney } from '../format'

const STATUS_TEXT = { green: 'Healthy', amber: 'Needs attention', red: 'Action needed' } as const

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : 'never')

type Props = {
  health: Health
  running: boolean
  onRun: () => void
  onFilterOwner: (userId: number | null) => void
}

/** Forecast Health: plain counts and sums over the open findings, with the formulas shown. */
export default function HealthSummary({ health, running, onRun, onFilterOwner }: Props) {
  const { last_run: last, last_success: ok } = health
  return (
    <section className="health" aria-label="Forecast health">
      <div className={`health-banner health-${health.status}`} role="status">
        <div>
          <strong>Forecast health: {STATUS_TEXT[health.status]}</strong>
          <div className="small">{health.status_reasons.join(' ')}</div>
        </div>
        <div className="text-end small">
          {last ? (
            <>
              Last check {when(last.finished_at ?? last.started_at)} ({last.trigger === 'scheduled' ? 'nightly' : 'on demand'},{' '}
              {last.status})
              {last.status === 'failed' && last.error_message ? `: ${last.error_message}` : ''}
              {ok && last.status === 'failed' ? <div>Last good run {when(ok.finished_at)}</div> : null}
            </>
          ) : (
            'The checks have not run yet.'
          )}
          {health.can_run && (
            <div>
              <button type="button" className="btn btn-outline-secondary btn-sm mt-1" disabled={running} onClick={onRun}>
                {running ? 'Running checks…' : 'Run checks now'}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="summary-cards" aria-label="Health totals">
        <div className="summary-card">
          <span className="summary-label">Open findings</span>
          <strong>{health.open_total}</strong>
          <span className="text-secondary small">
            {health.open_by_severity.error ?? 0} errors · {health.open_by_severity.warning ?? 0} warnings
          </span>
        </div>
        <div className="summary-card">
          <span className="summary-label">Revenue at stake</span>
          <strong>{formatMoney(health.revenue_at_stake)}</strong>
          <span className="text-secondary small">upper bound; findings can overlap</span>
        </div>
        <div className="summary-card">
          <span className="summary-label">Awaiting review</span>
          <strong>{health.awaiting_review}</strong>
          <span className="text-secondary small">investigated, needs a decision</span>
        </div>
        <div className="summary-card">
          <span className="summary-label">Unassigned</span>
          <strong>{health.unassigned_open}</strong>
          <span className="text-secondary small">no owner to route to</span>
        </div>
      </div>

      {health.owners.length > 0 && (
        <div className="table-responsive report-section">
          <table className="table table-sm align-middle mb-0" aria-label="Owners needing attention">
            <caption className="small text-secondary caption-top">Owners needing attention</caption>
            <thead>
              <tr>
                <th scope="col">Owner</th>
                <th scope="col" className="text-end">
                  Open
                </th>
                <th scope="col" className="text-end">
                  Revenue at stake
                </th>
                <th scope="col" className="text-end">
                  Oldest
                </th>
              </tr>
            </thead>
            <tbody>
              {health.owners.map((o) => (
                <tr key={o.user_id ?? 'none'}>
                  <th scope="row">
                    <button type="button" className="project-link" onClick={() => onFilterOwner(o.user_id)}>
                      {o.name}
                    </button>
                  </th>
                  <td className="text-end">{o.open_findings}</td>
                  <td className="text-end">{formatMoney(o.revenue_at_stake)}</td>
                  <td className="text-end">{o.oldest_days === 0 ? 'today' : `${o.oldest_days} d`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details className="chart-details">
        <summary>How these numbers are calculated</summary>
        <ul className="small mb-0 mt-2">
          {health.metrics.map((m) => (
            <li key={m.key}>
              <strong>{m.label}</strong> ({m.value}): {m.formula}.
            </li>
          ))}
          <li>
            <strong>Status</strong>: red if any open error finding or the latest run failed; amber if there are open warnings or no
            successful run in 36 hours; otherwise green. There is no hidden score.
          </li>
        </ul>
      </details>
    </section>
  )
}
