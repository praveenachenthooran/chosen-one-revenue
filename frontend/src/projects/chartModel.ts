import type { MonthColumn, ProjectDetail } from '../api'
import type { LineSeries } from '../charts/LineChart'
import { formatMonthLabel } from '../format'

const num = (value: string | null | undefined) => (value === null || value === undefined ? null : Number(value))

/**
 * One project's revenue over time, as the team described it: the original import as the reference line,
 * every later edit visible as the gap to the current forecast, and months or scope added after the import
 * on their own line. Actuals are not drawn here because uploaded actuals are company-wide by month.
 */
export function projectChartModel(detail: ProjectDetail, months: MonthColumn[]) {
  const byMonth = new Map(detail.monthly_revenue.map((m) => [m.month, m]))
  const filled = months.filter((m) => {
    const row = byMonth.get(m.month)
    return row && (Number(row.amount) > 0 || Number(row.baseline_amount ?? 0) > 0)
  })
  const shown = filled.length ? months.slice(months.indexOf(filled[0]), months.indexOf(filled[filled.length - 1]) + 1) : []
  const labels = shown.map((m) => formatMonthLabel(m.month))
  const series: LineSeries[] = [
    { label: 'Original import', color: '--muted', dashed: true, data: shown.map((m) => num(byMonth.get(m.month)?.baseline_amount)) },
    { label: 'Current forecast', color: '--brand', fill: true, data: shown.map((m) => num(byMonth.get(m.month)?.amount)) },
    {
      label: 'Added after import',
      color: '--potential',
      data: shown.map((m) => {
        const row = byMonth.get(m.month)
        return row && row.baseline_amount === null && Number(row.amount) > 0 ? Number(row.amount) : null
      }),
    },
  ]
  return { labels, series, shown }
}
