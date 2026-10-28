import { useMemo } from 'react'
import type { MonthColumn, ProjectDetail } from '../api'
import ChartLegend from '../charts/ChartLegend'
import LineChart from '../charts/LineChart'
import { projectChartModel } from './chartModel'


export default function ProjectChart({ detail, months, currentMonth }: { detail: ProjectDetail; months: MonthColumn[]; currentMonth: string }) {
  const { labels, series, shown } = useMemo(() => projectChartModel(detail, months), [detail, months])
  const marker = shown.findIndex((m) => m.month === currentMonth)
  if (shown.length === 0) {
    return (
      <div className="empty">
        <b>No forecast to chart yet</b>Enter monthly revenue for this project and it will appear here.
      </div>
    )
  }
  const hasAdded = series[2].data.some((v) => v !== null)
  return (
    <div className="stack" style={{ gap: 12 }}>
      <ChartLegend
        items={[
          { label: 'Original import', color: '--muted', kind: 'line', dashed: true },
          { label: 'Current forecast', color: '--brand', kind: 'line' },
          ...(hasAdded ? [{ label: 'Added after import', color: '--potential', kind: 'line' as const }] : []),
        ]}
      />
      <LineChart
        labels={labels}
        series={hasAdded ? series : series.slice(0, 2)}
        markerIndex={marker >= 0 ? marker : null}
        ariaLabel={`Monthly revenue for ${detail.customer}: original import and current forecast`}
      />
    </div>
  )
}
