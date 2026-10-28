import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { charts, liveChart } from '../test/chartMock'
import { json, mockApi, okRoutes, signedIn, type Routes } from '../test/fixtures'

vi.mock('chart.js', async () => (await import('../test/chartMock')).chartJsMock)

const owner = { ...signedIn, user: { id: 9, name: 'Marcus Lee', email: 'marcus@corp.test', roles: ['Commercial Owner'], can_edit_all: false } }

function mockFetch(overrides: Routes = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi({ ...okRoutes, '/api/auth/me': () => json(owner), ...overrides }))
}

beforeEach(() => {
  charts.length = 0
})
afterEach(() => vi.restoreAllMocks())

describe('Portfolio (Commercial Owner)', () => {
  it('lands on the portfolio showing only the projects the owner can edit', async () => {
    mockFetch()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Portfolio' })).toBeInTheDocument()
    expect(await screen.findByRole('row', { name: 'Open Cedarfall Energy' })).toBeInTheDocument()
    expect(screen.queryByRole('row', { name: 'Open Kestrel Life' })).not.toBeInTheDocument()
    expect(screen.queryByText('Revenue overview')).not.toBeInTheDocument() // not a leadership page
  })

  it('offers all projects read-only and never implies the owner can edit them', async () => {
    mockFetch()
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: /All projects/ }))

    const kestrel = await screen.findByRole('row', { name: 'Open Kestrel Life' })
    expect(within(kestrel).getByText('View only')).toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: 'Open Cedarfall Energy' })).queryByText('View only')).not.toBeInTheDocument()
  })

  it('draws a line chart below the table, open, of the backend monthly figures for the projects shown', async () => {
    mockFetch()
    render(<App />)
    await screen.findByRole('row', { name: 'Open Cedarfall Energy' })

    const chart = liveChart()!
    expect(chart.config.type).toBe('line')
    expect(chart.config.data.datasets).toHaveLength(1)
    expect(chart.config.data.labels).toEqual(['Oct 2026'])
    expect(chart.config.data.datasets[0].data).toEqual([1250.5]) // project 1's stored October amount

    const panel = screen.getByText('Portfolio revenue').closest('details') as HTMLDetailsElement
    expect(panel.open).toBe(true)
    const table = screen.getByRole('table')
    expect(table.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy() // graph is below
  })

  it('charts the sum of every project’s stored months when all projects are shown', async () => {
    mockFetch()
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: /All projects/ }))
    await screen.findByRole('row', { name: 'Open Kestrel Life' })

    const chart = liveChart()!
    expect(chart.config.data.labels).toEqual(['Sep 2026', 'Oct 2026'])
    expect(chart.config.data.datasets[0].data).toEqual([80000, 1250.5]) // matches /revenue/monthly-totals
  })

  it('filters the table and the chart together', async () => {
    mockFetch()
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: /All projects/ }))
    await screen.findByRole('row', { name: 'Open Kestrel Life' })

    await userEvent.selectOptions(screen.getByLabelText('Customer'), 'Kestrel Life')

    expect(screen.queryByRole('row', { name: 'Open Cedarfall Energy' })).not.toBeInTheDocument()
    expect(liveChart()!.config.data.datasets[0].data).toEqual([80000])
  })

  it('links CSV and Excel exports that carry the filters', async () => {
    mockFetch()
    render(<App />)
    await screen.findByRole('row', { name: 'Open Cedarfall Energy' })
    expect(screen.getByRole('link', { name: /Export Excel/ })).toHaveAttribute('href', '/api/v1/forecast/export?format=xlsx')
    expect(screen.getByRole('link', { name: /Export CSV/ })).toHaveAttribute('href', '/api/v1/forecast/export?format=csv&view=projects')

    await userEvent.selectOptions(screen.getByLabelText('Practice'), 'CRM')
    expect(screen.getByRole('link', { name: /Export Excel/ })).toHaveAttribute('href', '/api/v1/forecast/export?practice=CRM&format=xlsx')
  })

  it('shows the status in plain words, with no technology or mismatch columns', async () => {
    mockFetch()
    render(<App />)
    const table = await screen.findByRole('table')
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Project', 'Practice', 'Region', 'Industry', 'Billing', 'Total forecast', 'Status', 'Updated'])
    expect(within(table).getByText('Off Track')).toBeInTheDocument()
  })
})
