import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  exportUrl,
  fetchActualMonths,
  fetchFilterOptions,
  fetchForecast,
  fetchSnapshot,
  fetchSnapshots,
  type ActualMonth,
  type FilterOptions,
  type ForecastFilters,
  type ForecastTotals,
  type Snapshot,
  type SnapshotTotals,
} from '../api'
import BarChart from '../charts/BarChart'
import ChartLegend from '../charts/ChartLegend'
import { formatDate, formatMoney, formatMoneyShort, formatMonthLabel } from '../format'
import DetectiveDrawer from '../quality/DetectiveDrawer'
import { icons } from '../ui/icons'
import { ErrorState, KpiStrip, Loading, PageHeader } from '../ui/Panel'
import { useWorkspace } from '../workspaceContext'

type Props = {
  onSessionEnded: () => void
  onOpenProject: (projectId: number) => void
  onOpenDeal: (deal: { id: number; title: string }) => void
  onGo: (view: 'checks' | 'snapshots') => void
}

const FIELDS: { key: keyof ForecastFilters; label: string; options: (o: FilterOptions) => [string, string][] }[] = [
  { key: 'practice', label: 'Practice', options: (o) => o.practices.map((v) => [v, v]) },
  { key: 'region', label: 'Region', options: (o) => o.regions.map((v) => [v, v]) },
  { key: 'customer', label: 'Customer', options: (o) => o.customers.map((v) => [v, v]) },
  { key: 'owner', label: 'Owner', options: (o) => o.owners.map((v) => [v.code, v.name]) },
  { key: 'industry', label: 'Industry', options: (o) => o.industries.map((v) => [v, v]) },
  { key: 'category', label: 'Category', options: (o) => o.categories.map((v) => [v, v]) },
]

const num = (v: string) => Number(v)
const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/** Sales Leadership: how is company revenue changing? Estimated, actual and potential by month. */
export default function RevenueOverview({ onSessionEnded, onOpenProject, onOpenDeal, onGo }: Props) {
  const ws = useWorkspace()
  const [filters, setFilters] = useState<ForecastFilters>({})
  const [forecast, setForecast] = useState<ForecastTotals | null>(null)
  const [failed, setFailed] = useState(false)
  const [actuals, setActuals] = useState<ActualMonth[]>([])
  const [options, setOptions] = useState<FilterOptions | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [compareId, setCompareId] = useState<number | null>(null)
  const [compare, setCompare] = useState<SnapshotTotals | null>(null)
  const [openFinding, setOpenFinding] = useState<number | null>(null)

  const handle = useCallback(
    (error: unknown) => {
      if (isAbort(error)) return
      if (error instanceof ApiError && error.status === 401) onSessionEnded()
      else setFailed(true)
    },
    [onSessionEnded],
  )

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    fetchFilterOptions(signal).then(setOptions).catch(() => undefined)
    fetchActualMonths(signal).then(setActuals).catch(() => setActuals([]))
    fetchSnapshots(signal)
      .then((list) => {
        setSnapshots(list.items)
        setCompareId((current) => current ?? list.items[0]?.id ?? null)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchForecast(filters, controller.signal)
      .then((f) => {
        setForecast(f)
        setFailed(false)
      })
      .catch(handle)
    return () => controller.abort()
  }, [filters, handle, ws.revision])

  useEffect(() => {
    if (compareId === null) return
    const controller = new AbortController()
    fetchSnapshot(compareId, filters, controller.signal)
      .then(setCompare)
      .catch(() => setCompare(null))
    return () => controller.abort()
  }, [compareId, filters])

  const filtered = Object.values(filters).some(Boolean)
  const months = useMemo(() => forecast?.months ?? [], [forecast])
  const nearTerm = useMemo<[number, number] | null>(() => {
    const here = months.findIndex((m) => m.month === ws.currentMonth)
    return here >= 0 ? [here, Math.min(here + 2, months.length - 1)] : null
  }, [months, ws.currentMonth])

  const chart = useMemo(() => {
    const byMonth = new Map(actuals.map((a) => [a.month, Number(a.actual_total)]))
    const series = [
      { label: 'Estimated', color: '--forecast', fadeLongTerm: true, data: months.map((m) => num(m.committed)) },
      ...(filtered || actuals.length === 0 ? [] : [{ label: 'Actual', color: '--actual', data: months.map((m) => byMonth.get(m.month) ?? null) }]),
      { label: 'Potential', color: '--potential', fadeLongTerm: true, data: months.map((m) => num(m.weighted)) },
    ]
    return { labels: months.map((m) => formatMonthLabel(m.month)), series }
  }, [months, actuals, filtered])

  const horizon = useMemo(() => {
    if (!nearTerm) return null
    const sumOf = (slice: typeof months, pick: 'committed' | 'weighted' | 'total') => slice.reduce((a, m) => a + num(m[pick]), 0)
    const near = months.slice(nearTerm[0], nearTerm[1] + 1)
    const later = months.slice(nearTerm[1] + 1)
    return { near, later, sumOf }
  }, [months, nearTerm])

  const exceptions = useMemo(() => {
    if (ws.state.kind !== 'ready') return []
    const names = new Map(ws.state.data.projects.map((p) => [p.id, `${p.customer}: ${p.name}`]))
    return ws.state.data.findings
      .filter((f) => f.status === 'open' && f.severity !== 'info')
      .sort((a, b) => num(b.financial_impact) - num(a.financial_impact))
      .slice(0, 5)
      .map((f) => ({ finding: f, project: f.project_id ? (names.get(f.project_id) ?? '') : '' }))
  }, [ws.state])

  if (failed && !forecast) return <ErrorState text="The revenue overview could not be loaded." onRetry={() => setFilters({ ...filters })} />
  if (!forecast) return <Loading text="Loading the revenue overview…" />

  const health = ws.state.kind === 'ready' ? ws.state.data.health : null
  const delta = compare
    ? {
        committed: num(forecast.committed_total) - num(compare.committed_total),
        weighted: num(forecast.weighted_total) - num(compare.weighted_total),
        combined: num(forecast.combined_total) - num(compare.combined_total),
      }
    : null
  const movers = compare
    ? months
        .map((m) => {
          const before = compare.months.find((c) => c.month === m.month)
          return { month: m.month, change: num(m.total) - num(before?.total ?? '0'), before: num(before?.total ?? '0'), after: num(m.total) }
        })
        .filter((r) => Math.abs(r.change) >= 1)
        .sort((a, b) => Math.abs(b.change) - Math.abs(a.change))
        .slice(0, 5)
    : []
  const signed = (v: number) => `${v >= 0 ? '+' : '-'}${formatMoney(Math.abs(v))}`

  return (
    <>
      <PageHeader
        title="Revenue overview"
        sub="Company-wide revenue, the forecast ahead and emerging risks."
        actions={
          <>
            <a className="btn" href={exportUrl({ snapshotId: null }, filters, 'csv', 'monthly')} download>
              {icons.download}Export CSV
            </a>
            <a className="btn btn-primary" href={exportUrl({ snapshotId: null }, filters, 'xlsx')} download>
              {icons.download}Export Excel
            </a>
          </>
        }
      />
      <div className="stack">
        <section className="panel" aria-label="Filters">
          <div className="filters" style={{ ['--filter-cols' as string]: 5 }}>
            {FIELDS.map((f) => (
              <div className="field" key={f.key}>
                <label htmlFor={`lf-${f.key}`}>{f.label}</label>
                <select
                  id={`lf-${f.key}`}
                  className="form-select"
                  value={filters[f.key] ?? ''}
                  onChange={(e) => setFilters((cur) => ({ ...cur, [f.key]: e.target.value || undefined }))}
                >
                  <option value="">All</option>
                  {options && f.options(options).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
          {filtered && (
            <div className="fsummary">
              <span>Filters apply to the forecast and pipeline figures below.</span>
              <button type="button" className="linkbtn" onClick={() => setFilters({})}>
                Clear filters
              </button>
            </div>
          )}
        </section>

        <KpiStrip
          items={[
            { label: 'Committed revenue', value: formatMoneyShort(forecast.committed_total), note: 'Projects already running or signed' },
            { label: 'Weighted pipeline', value: formatMoneyShort(forecast.weighted_total), note: `${forecast.weighted_deals} open deals, by probability` },
            { label: 'Combined forecast', value: formatMoneyShort(forecast.combined_total), note: 'Committed plus weighted pipeline' },
            {
              label: 'Revenue at risk',
              value: health ? formatMoneyShort(health.revenue_at_stake) : '—',
              note: health ? `${health.open_total} open exceptions` : undefined,
              tone: health && health.open_total > 0 ? 'bad' : 'good',
            },
          ]}
        />

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Revenue by month</h2>
              <p className="hint">
                {filtered || actuals.length === 0
                  ? 'Actual revenue is company-wide, so it is not shown while filters are on.'
                  : 'Estimated and potential revenue are forecasts; later months are lighter because they are less certain.'}
              </p>
            </div>
            <ChartLegend
              items={[
                { label: 'Estimated (running projects)', color: '--forecast' },
                ...(filtered || actuals.length === 0 ? [] : [{ label: 'Actual', color: '--actual' }]),
                { label: 'Potential (not yet accepted)', color: '--potential' },
              ]}
            />
          </div>
          {months.length ? (
            <BarChart labels={chart.labels} series={chart.series} nearTerm={nearTerm} size="tall" ariaLabel="Estimated, actual and potential revenue by month" />
          ) : (
            <div className="empty">
              <b>No forecast for these filters</b>Clear a filter to see revenue by month.
            </div>
          )}
        </section>

        <div className="two-even">
          <section className="panel panel-flush">
            <div className="panel-head">
              <div>
                <h2>Fiscal-year outlook</h2>
                <p className="hint">Forecast by fiscal year.</p>
              </div>
            </div>
            <div className="tscroll">
              <table className="tbl">
                <thead>
                  <tr>
                    <th style={{ paddingLeft: 22 }}>Year</th>
                    <th className="r">Committed</th>
                    <th className="r">Weighted pipeline</th>
                    <th className="r" style={{ paddingRight: 22 }}>Combined</th>
                  </tr>
                </thead>
                <tbody>
                  {forecast.fiscal_years.map((fy) => (
                    <tr key={fy.fiscal_year}>
                      <td style={{ paddingLeft: 22 }}>
                        <b>{fy.fiscal_year}</b>
                      </td>
                      <td className="r">{formatMoney(fy.committed)}</td>
                      <td className="r">{formatMoney(fy.weighted)}</td>
                      <td className="r" style={{ paddingRight: 22 }}>
                        <b>{formatMoney(fy.total)}</b>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel panel-flush">
            <div className="panel-head">
              <div>
                <h2>Near term and beyond</h2>
                <p className="hint">Later months depend on projects and deals that may still change.</p>
              </div>
            </div>
            {horizon ? (
              <div className="tscroll">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th style={{ paddingLeft: 22 }}>Period</th>
                      <th className="r">Committed</th>
                      <th className="r">Weighted pipeline</th>
                      <th className="r" style={{ paddingRight: 22 }}>Combined</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { label: 'Next 3 months', slice: horizon.near },
                      { label: 'Later months', slice: horizon.later },
                    ].map((row) => (
                      <tr key={row.label}>
                        <td style={{ paddingLeft: 22 }}>
                          <b>{row.label}</b>
                          <div className="hint">
                            {row.slice.length ? `${formatMonthLabel(row.slice[0].month)} to ${formatMonthLabel(row.slice[row.slice.length - 1].month)}` : 'No months'}
                          </div>
                        </td>
                        <td className="r">{formatMoney(horizon.sumOf(row.slice, 'committed'))}</td>
                        <td className="r">{formatMoney(horizon.sumOf(row.slice, 'weighted'))}</td>
                        <td className="r" style={{ paddingRight: 22 }}>
                          <b>{formatMoney(horizon.sumOf(row.slice, 'total'))}</b>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">The current month is outside the forecast window.</div>
            )}
          </section>
        </div>

        <div className="two-even">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Forecast changes</h2>
                <p className="hint">Today’s forecast compared with a saved snapshot.</p>
              </div>
              {snapshots.length > 0 && (
                <select className="form-select form-select-sm" style={{ width: 'auto' }} aria-label="Compare with snapshot" value={compareId ?? ''} onChange={(e) => setCompareId(Number(e.target.value))}>
                  {snapshots.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
              )}
            </div>
            {!compare || !delta ? (
              <div className="empty">
                <b>No snapshot to compare with</b>Snapshots are saved automatically each month.
              </div>
            ) : (
              <>
                <div className="rows">
                  <div>
                    <span>Committed</span>
                    <b className={delta.committed === 0 ? '' : delta.committed > 0 ? 'num-good' : 'num-bad'}>{signed(delta.committed)}</b>
                  </div>
                  <div>
                    <span>Weighted pipeline</span>
                    <b className={delta.weighted === 0 ? '' : delta.weighted > 0 ? 'num-good' : 'num-bad'}>{signed(delta.weighted)}</b>
                  </div>
                  <div>
                    <span>Combined</span>
                    <b className={delta.combined === 0 ? '' : delta.combined > 0 ? 'num-good' : 'num-bad'}>{signed(delta.combined)}</b>
                  </div>
                </div>
                <p className="hint mt-2">
                  Snapshot taken {formatDate(compare.snapshot.taken_at)}.{' '}
                  <button type="button" className="linkbtn" onClick={() => onGo('snapshots')}>
                    View snapshots
                  </button>
                </p>
                {movers.length > 0 && (
                  <table className="tbl mt-2">
                    <thead>
                      <tr>
                        <th>Biggest movements</th>
                        <th className="r">Then</th>
                        <th className="r">Now</th>
                        <th className="r">Change</th>
                      </tr>
                    </thead>
                    <tbody>
                      {movers.map((r) => (
                        <tr key={r.month}>
                          <td>{formatMonthLabel(r.month)}</td>
                          <td className="r">{formatMoneyShort(r.before)}</td>
                          <td className="r">{formatMoneyShort(r.after)}</td>
                          <td className={`r ${r.change > 0 ? 'num-good' : 'num-bad'}`}>{signed(r.change)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Risks and exceptions</h2>
                <p className="hint">The largest open items found by the checks.</p>
              </div>
              <button type="button" className="linkbtn" onClick={() => onGo('checks')}>
                See all checks
              </button>
            </div>
            {exceptions.length === 0 ? (
              <p className="ok-note">
                {icons.check}
                No open exceptions.
              </p>
            ) : (
              <ul className="flags">
                {exceptions.map(({ finding, project }) => (
                  <li key={finding.id} className={finding.severity === 'error' ? 'high' : ''}>
                    <b>{finding.title}</b>
                    <p>{finding.summary}</p>
                    <small>
                      {project && `${project} · `}
                      {formatMoney(finding.financial_impact)} at stake
                    </small>
                    <div className="flag-actions">
                      <button type="button" className="btn btn-primary btn-sm" onClick={() => setOpenFinding(finding.id)}>
                        Open Revenue Detective
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      {openFinding !== null && (
        <DetectiveDrawer
          findingId={openFinding}
          onClose={() => setOpenFinding(null)}
          onChanged={() => void ws.reloadFindings()}
          onSessionEnded={onSessionEnded}
          onOpenProject={(id) => {
            setOpenFinding(null)
            onOpenProject(id)
          }}
          onOpenDeal={(deal) => {
            setOpenFinding(null)
            onOpenDeal(deal)
          }}
        />
      )}
    </>
  )
}
