import { useMemo, useState } from 'react'
import { exportUrl, type AuthUser, type ForecastFilters } from '../api'
import ChartLegend from '../charts/ChartLegend'
import LineChart from '../charts/LineChart'
import { formatDate, formatMoney, formatMoneyShort, formatMonthLabel } from '../format'
import FilterBar from '../projects/FilterBar'
import { applyFilters, NO_FILTERS, type ProjectFilters } from '../projects/filters'
import { STATUS_LABEL, STATUS_TONE } from '../projects/status'
import { icons } from '../ui/icons'
import { ErrorState, Fold, KpiStrip, Loading, PageHeader } from '../ui/Panel'
import { useWorkspace } from '../workspaceContext'

type Props = {
  user: AuthUser
  title: string
  onOpen: (projectId: number) => void
}

const sum = (values: number[]) => values.reduce((a, b) => a + b, 0)

/** The server's export filters understand customer, practice, region and search; they match the on-screen filters. */
function exportFilters(f: ProjectFilters): ForecastFilters {
  return { customer: f.customer || undefined, practice: f.practice || undefined, region: f.region || undefined, q: f.q.trim() || undefined }
}

/** Commercial Owner portfolio (also the read-only "All projects" view for roles without a landing page of their own). */
export default function PortfolioView({ user, title, onOpen }: Props) {
  const ws = useWorkspace()
  const [filters, setFilters] = useState<ProjectFilters>(NO_FILTERS)
  const [scope, setScope] = useState<'mine' | 'all'>('mine')

  const ownedCount = useMemo(() => ws.rows.filter((r) => r.project.can_edit).length, [ws.rows])
  const scoped = useMemo(() => {
    const everything = user.can_edit_all || ownedCount === 0 || scope === 'all'
    return everything ? ws.rows : ws.rows.filter((r) => r.project.can_edit)
  }, [ws.rows, user.can_edit_all, ownedCount, scope])
  const shown = useMemo(() => applyFilters(scoped, filters), [scoped, filters])

  const months = useMemo(() => (ws.state.kind === 'ready' ? ws.state.data.months : []), [ws.state])
  const chart = useMemo(() => {
    const totals = months.map((m) => sum(shown.map((r) => Number(r.project.monthly?.[m.month] ?? 0))))
    const first = totals.findIndex((t) => t > 0)
    const last = totals.length - 1 - [...totals].reverse().findIndex((t) => t > 0)
    if (first < 0) return null
    const slice = months.slice(first, last + 1)
    return { labels: slice.map((m) => formatMonthLabel(m.month)), data: totals.slice(first, last + 1), months: slice }
  }, [months, shown])

  if (ws.state.kind === 'loading') return <Loading text="Loading the portfolio…" />
  if (ws.state.kind === 'error') return <ErrorState text="The portfolio could not be loaded." onRetry={ws.reload} />

  const here = months.findIndex((m) => m.month === ws.currentMonth)
  const next3 = here >= 0 ? months.slice(here, here + 3) : []
  const fy = here >= 0 ? months[here].fiscal_year : null
  const monthTotal = (keys: string[]) => sum(shown.flatMap((r) => keys.map((k) => Number(r.project.monthly?.[k] ?? 0))))
  const attention = shown.filter((r) => r.status !== 'on_track').length
  const canSwitchScope = !user.can_edit_all && ownedCount > 0 && ownedCount < ws.rows.length
  const markerIndex = chart ? chart.months.findIndex((m) => m.month === ws.currentMonth) : -1

  return (
    <>
      <PageHeader
        title={title}
        sub={scoped.length === ws.rows.length ? 'Every project and its expected revenue.' : 'The projects you own and their expected revenue.'}
        actions={
          <>
            <a className="btn" href={exportUrl({ snapshotId: null }, exportFilters(filters), 'csv', 'projects')} download>
              {icons.download}Export CSV
            </a>
            <a className="btn btn-primary" href={exportUrl({ snapshotId: null }, exportFilters(filters), 'xlsx')} download>
              {icons.download}Export Excel
            </a>
          </>
        }
      />
      <div className="stack">
        <KpiStrip
          items={[
            { label: 'Projects', value: String(shown.length) },
            { label: 'Forecast, next 3 months', value: formatMoneyShort(monthTotal(next3.map((m) => m.month))), note: next3.length ? `${next3[0].label} to ${next3[next3.length - 1].label}` : undefined },
            { label: fy ? `Forecast, ${fy}` : 'Forecast, fiscal year', value: formatMoneyShort(monthTotal(months.filter((m) => m.fiscal_year === fy).map((m) => m.month))) },
            { label: 'Need attention', value: String(attention), note: attention ? 'Off Track or Missing Entry' : 'Everything on track', tone: attention ? 'bad' : 'good' },
          ]}
        />
        <FilterBar
          fields={['customer', 'practice', 'region', 'month']}
          rows={scoped}
          months={months}
          value={filters}
          onChange={setFilters}
          shown={shown.length}
          searchLabel="Project or customer"
        />
        <section className="panel panel-flush">
          {canSwitchScope && (
            <div className="tabs" role="tablist" style={{ padding: '0 22px' }}>
              <button type="button" role="tab" aria-selected={scope === 'mine'} className={`tab ${scope === 'mine' ? 'on' : ''}`} onClick={() => setScope('mine')}>
                My projects<span className="c">{ownedCount}</span>
              </button>
              <button type="button" role="tab" aria-selected={scope === 'all'} className={`tab ${scope === 'all' ? 'on' : ''}`} onClick={() => setScope('all')}>
                All projects<span className="c">{ws.rows.length}</span>
              </button>
            </div>
          )}
          {shown.length === 0 ? (
            <div className="empty">
              <b>No projects match these filters</b>
              <button type="button" className="linkbtn" onClick={() => setFilters(NO_FILTERS)}>
                Clear filters
              </button>
            </div>
          ) : (
            <div className="tscroll">
              <table className="tbl" style={{ minWidth: 900 }}>
                <thead>
                  <tr>
                    <th style={{ paddingLeft: 22 }}>Project</th>
                    <th>Practice</th>
                    <th>Region</th>
                    <th>Industry</th>
                    <th>Billing</th>
                    <th className="r">Total forecast</th>
                    <th>Status</th>
                    <th style={{ paddingRight: 22 }}>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr
                      key={r.project.id}
                      data-open
                      tabIndex={0}
                      aria-label={`Open ${r.project.customer}`}
                      onClick={() => onOpen(r.project.id)}
                      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onOpen(r.project.id))}
                    >
                      <td className="pname" style={{ paddingLeft: 22 }}>
                        <b>{r.project.customer}</b>
                        <span>
                          {r.project.name}
                          {!r.project.can_edit && <span className="pill ms-2">View only</span>}
                        </span>
                      </td>
                      <td>{r.project.practice ?? '—'}</td>
                      <td>{r.project.region ?? '—'}</td>
                      <td>{r.project.industry ?? '—'}</td>
                      <td>{r.project.billing_type ?? '—'}</td>
                      <td className="r">{formatMoney(r.project.total_revenue)}</td>
                      <td>
                        <span className={`rag ${STATUS_TONE[r.status]}`}>{STATUS_LABEL[r.status]}</span>
                      </td>
                      <td style={{ paddingRight: 22 }}>{r.project.last_updated ? formatDate(r.project.last_updated) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <Fold title="Portfolio revenue" hint="Expected revenue by month for the projects shown above." defaultOpen>
          {chart ? (
            <div className="stack" style={{ gap: 12 }}>
              <ChartLegend items={[{ label: 'Expected revenue', color: '--brand', kind: 'line' }]} />
              <LineChart
                labels={chart.labels}
                series={[{ label: 'Expected revenue', color: '--brand', fill: true, data: chart.data }]}
                markerIndex={markerIndex >= 0 ? markerIndex : null}
                size="tall"
                ariaLabel={`Expected monthly revenue for ${shown.length} projects`}
              />
            </div>
          ) : (
            <div className="empty">
              <b>No revenue to chart for these filters</b>Change the filters to see expected revenue by month.
            </div>
          )}
        </Fold>
      </div>
    </>
  )
}
