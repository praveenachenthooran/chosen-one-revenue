import { describe, expect, it } from 'vitest'
import type { MonthColumn, ProjectDetail } from '../api'
import { projectChartModel } from './chartModel'

const month = (m: string, label: string): MonthColumn => ({ month: m, label, fiscal_year: 'FY27', fiscal_period: 1, is_closed: false })
const MONTHS = [
  month('2026-09-01', 'Sep 2026'),
  month('2026-10-01', 'Oct 2026'),
  month('2026-11-01', 'Nov 2026'),
  month('2026-12-01', 'Dec 2026'),
  month('2027-01-01', 'Jan 2027'),
]
const row = (m: string, amount: string, baseline: string | null, edited = false) => ({ month: m, fiscal_year: 'FY27', amount, baseline_amount: baseline, edited, source_cell: null })

const detail = (monthly_revenue: ProjectDetail['monthly_revenue']) => ({ monthly_revenue }) as ProjectDetail

describe('per-project chart model', () => {
  it('draws the original import and the current forecast from the stored values', () => {
    const { series, labels } = projectChartModel(
      detail([row('2026-10-01', '1250.50', '1000.00', true), row('2026-11-01', '900.00', '900.00')]),
      MONTHS,
    )
    expect(labels).toEqual(['Oct 2026', 'Nov 2026'])
    expect(series[0]).toMatchObject({ label: 'Original import', data: [1000, 900] })
    expect(series[1]).toMatchObject({ label: 'Current forecast', data: [1250.5, 900] })
  })

  it('puts months added after the import on their own line', () => {
    const { series } = projectChartModel(
      detail([row('2026-10-01', '1000.00', '1000.00'), row('2026-11-01', '1000.00', '1000.00'), row('2026-12-01', '800.00', null)]),
      MONTHS,
    )
    expect(series[2]).toMatchObject({ label: 'Added after import', data: [null, null, 800] })
    expect(series[0].data).toEqual([1000, 1000, null]) // the import had no December
  })

  it('trims empty months from both ends and keeps gaps inside the range', () => {
    const { labels } = projectChartModel(detail([row('2026-10-01', '10.00', '10.00'), row('2027-01-01', '10.00', '10.00')]), MONTHS)
    expect(labels).toEqual(['Oct 2026', 'Nov 2026', 'Dec 2026', 'Jan 2027'])
  })

  it('has nothing to chart for a project without a forecast', () => {
    expect(projectChartModel(detail([]), MONTHS).shown).toHaveLength(0)
  })
})
