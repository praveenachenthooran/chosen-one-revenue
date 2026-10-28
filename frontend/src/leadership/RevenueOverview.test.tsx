import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { charts, liveChart } from '../test/chartMock'
import { croMe, filterOptions, json, mockApi, okRoutes, snapshotTotals, type Routes } from '../test/fixtures'

vi.mock('chart.js', async () => (await import('../test/chartMock')).chartJsMock)

function mockFetch(overrides: Routes = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(
    mockApi({ ...okRoutes, '/api/auth/me': () => json(croMe), '/api/v1/snapshots/2': () => json(snapshotTotals), ...overrides }),
  )
}
const urls = () => vi.mocked(fetch).mock.calls.map(([u]) => String(u))

beforeEach(() => {
  charts.length = 0
})
afterEach(() => vi.restoreAllMocks())

describe('Revenue overview (Sales Leadership)', () => {
  it('lands Sales Leadership on the overview with the committed, weighted and combined totals from the server', async () => {
    mockFetch()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Revenue overview' })).toBeInTheDocument()
    const kpis = within(await screen.findByText('Committed revenue').then((el) => el.closest('.kpis') as HTMLElement))
    expect(kpis.getByText('$9K')).toBeInTheDocument()
    expect(kpis.getByText('$10K')).toBeInTheDocument()
    expect(kpis.getByText('$19K')).toBeInTheDocument()
  })

  it('draws Michael’s bar graph: estimated, actual and potential, from the backend figures', async () => {
    mockFetch()
    render(<App />)
    await screen.findByText('Revenue by month')

    const chart = liveChart()!
    expect(chart.config.type).toBe('bar')
    expect(chart.config.data.labels).toEqual(['Oct 2026', 'Nov 2026'])
    const byLabel = Object.fromEntries(chart.config.data.datasets.map((d) => [d.label, d.data]))
    expect(Object.keys(byLabel)).toEqual(['Estimated', 'Actual', 'Potential'])
    expect(byLabel.Estimated).toEqual([3000, 6000]) // committed forecast of running projects
    expect(byLabel.Actual).toEqual([3000, null]) // uploaded actuals; none yet for November
    expect(byLabel.Potential).toEqual([0, 10000]) // probability-weighted pipeline
    expect(screen.getByRole('img', { name: /Estimated, actual and potential revenue by month/ })).toBeInTheDocument()
  })

  it('marks the next three months so the near term reads differently from the longer term', async () => {
    mockFetch()
    render(<App />)
    await screen.findByText('Revenue by month')
    expect(liveChart()!.config.plugins?.map((p) => p.id)).toContain('nearTermBand')
    expect(await screen.findByText('Next 3 months')).toBeInTheDocument()
    expect(screen.getByText('Later months')).toBeInTheDocument()
  })

  it('shows the fiscal-year outlook exactly as the server calculated it', async () => {
    mockFetch()
    render(<App />)
    const section = (await screen.findByText('Fiscal-year outlook')).closest('section') as HTMLElement
    const row = within(section).getByRole('row', { name: /FY27/ })
    expect(within(row).getByText('$9,000')).toBeInTheDocument()
    expect(within(row).getByText('$10,000')).toBeInTheDocument()
    expect(within(row).getByText('$19,000')).toBeInTheDocument()
  })

  it('passes a filter to the server and stops showing company-wide actuals', async () => {
    mockFetch({ '/api/v1/pipeline/filter-options': () => json({ ...filterOptions, regions: ['NA', 'UK'] }) })
    render(<App />)
    await screen.findByText('Revenue by month')
    await within(screen.getByLabelText('Region').closest('div') as HTMLElement).findByRole('option', { name: 'UK' })

    await userEvent.selectOptions(screen.getByLabelText('Region'), 'UK')

    await screen.findByText(/Actual revenue is company-wide, so it is not shown while filters are on/)
    expect(urls().some((u) => u.includes('/v1/forecast/monthly?region=UK'))).toBe(true)
    expect(liveChart()!.config.data.datasets.map((d) => d.label)).toEqual(['Estimated', 'Potential'])
  })

  it('compares today’s forecast with a snapshot and says how much it moved', async () => {
    mockFetch()
    render(<App />)
    const section = (await screen.findByText('Forecast changes')).closest('section') as HTMLElement
    // Today $9,000 / $10,000 / $19,000 against the snapshot's $18,000 / $60,000 / $78,000.
    expect(await within(section).findByText('-$9,000')).toBeInTheDocument()
    expect(within(section).getByText('-$50,000')).toBeInTheDocument()
    expect(within(section).getByText('-$59,000')).toBeInTheDocument()
  })

  it('lists the largest open exceptions and opens the Revenue Detective for one', async () => {
    mockFetch()
    render(<App />)
    const section = (await screen.findByText('Risks and exceptions')).closest('section') as HTMLElement
    const items = within(section).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Closed Won not committed') // $186,000: the largest, so first
    expect(items[1]).toHaveTextContent('Missing owner') // $120,000
    expect(items[2]).toHaveTextContent('Unusual revenue rise') // $21,000
    await userEvent.click(within(items[0]).getByRole('button', { name: 'Open Revenue Detective' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('links exports that carry the filters', async () => {
    mockFetch()
    render(<App />)
    await screen.findByRole('heading', { name: 'Revenue overview' })
    expect(screen.getByRole('link', { name: /Export Excel/ })).toHaveAttribute('href', '/api/v1/forecast/export?format=xlsx')
  })
})
