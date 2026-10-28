import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { charts, liveChart } from '../test/chartMock'
import { croMe, health, json, mockApi, okRoutes, projects, signedIn, type Routes } from '../test/fixtures'

vi.mock('chart.js', async () => (await import('../test/chartMock')).chartJsMock)

const EDIT_URL = 'PUT /api/v1/projects/1/revenue/2026-10-01'
const owner = { ...signedIn, user: { id: 9, name: 'Marcus Lee', email: 'm@corp.test', roles: ['Commercial Owner'], can_edit_all: false } }
const detailRoute = (okRoutes['/api/v1/projects/1'] as () => Response)().clone()

function mockFetch(overrides: Routes = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi({ ...okRoutes, ...overrides }))
}
const puts = () =>
  vi
    .mocked(fetch)
    .mock.calls.filter(([, init]) => init?.method === 'PUT')
    .map(([url, init]) => ({ url: String(url), body: JSON.parse(String(init?.body)) as Record<string, unknown>, headers: init?.headers as Record<string, string> }))

async function openProject() {
  await userEvent.click(await screen.findByRole('row', { name: 'Open Cedarfall Energy' }))
  await screen.findByText('Monthly forecast')
}

beforeEach(() => {
  charts.length = 0
})
afterEach(() => vi.restoreAllMocks())

describe('Project page', () => {
  it('shows the summary, what needs attention and the change history', async () => {
    mockFetch()
    render(<App />)
    await openProject()

    expect(screen.getByRole('heading', { name: /Cedarfall Energy/ })).toHaveTextContent('Off Track')
    expect(screen.getByText('Practice')).toBeInTheDocument()
    const attention = screen.getByText('Needs attention').closest('section') as HTMLElement
    expect(within(attention).getByText(/with no reason recorded/)).toBeInTheDocument()
    expect(within(attention).getByText(/\$21,000 at stake/)).toBeInTheDocument()
    const history = screen.getByText('Change history').closest('details') as HTMLElement
    expect(await within(history).findByText(/\$1,000\.00 → \$1,250\.50/)).toBeInTheDocument()
    expect(within(history).getByText(/Client added scope/)).toBeInTheDocument()
  })

  it('never shows raw JSON or database identifiers in the main view', async () => {
    mockFetch()
    render(<App />)
    await openProject()
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/[{}]\s*"|":\s*"|source_row_id|project_id/)
  })

  it('keeps the graph collapsed for both a Client Partner and a Commercial Owner', async () => {
    mockFetch()
    const partner = render(<App />)
    await openProject()
    expect((screen.getByText('Monthly revenue').closest('details') as HTMLDetailsElement).open).toBe(false)
    partner.unmount()

    vi.restoreAllMocks()
    mockFetch({ '/api/auth/me': () => json(owner) })
    render(<App />)
    await openProject()
    expect((screen.getByText('Monthly revenue').closest('details') as HTMLDetailsElement).open).toBe(false)
  })

  it('draws the original import against the current forecast as lines', async () => {
    mockFetch()
    render(<App />)
    await openProject()

    const chart = liveChart()!
    expect(chart.config.type).toBe('line')
    const byLabel = Object.fromEntries(chart.config.data.datasets.map((d) => [d.label, d.data]))
    expect(byLabel['Original import']).toEqual([1000]) // baseline from the import
    expect(byLabel['Current forecast']).toEqual([1250.5]) // the stored, edited amount
  })

  it('saves an edit with the previous value, then the graph follows the backend’s new value', async () => {
    let amount = '1250.50'
    const detail = (await detailRoute.json()) as { monthly_revenue: { amount: string }[] }
    mockFetch({
      [EDIT_URL]: () => {
        amount = '1500.00'
        return json({ project_id: 1, month: '2026-10-01', amount, previous_amount: '1250.50', changed: true, project_total: amount, revision_id: 9 })
      },
      '/api/v1/projects/1': () => json({ ...detail, monthly_revenue: [{ ...detail.monthly_revenue[0], amount }] }),
    })
    render(<App />)
    await openProject()

    await userEvent.type(screen.getByLabelText(/Reason for changes/), 'Client added scope')
    const input = screen.getByLabelText('Revenue for Oct 2026')
    await userEvent.clear(input)
    await userEvent.type(input, '1500{Enter}')

    await waitFor(() => expect(puts()).toHaveLength(1))
    expect(puts()[0].body).toEqual({ amount: '1500', expected_amount: '1250.50', reason: 'Client added scope' })
    expect(puts()[0].headers['X-CSRF-Token']).toBe('csrf-token-123')
    expect(await screen.findByText('Saved Oct 2026 for Data Platform Build.')).toBeInTheDocument()
    await waitFor(() => {
      const series = liveChart()!.config.data.datasets.find((d) => d.label === 'Current forecast')!
      expect(series.data).toEqual([1500])
    })
  })

  it('rejects an invalid amount without calling the server', async () => {
    mockFetch()
    render(<App />)
    await openProject()
    const input = screen.getByLabelText('Revenue for Oct 2026')
    await userEvent.clear(input)
    await userEvent.type(input, '=90000-55000{Enter}')

    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent(/enter a plain number/i)
    expect(puts()).toHaveLength(0)
  })

  it('is read-only for a project the user does not own', async () => {
    mockFetch({ '/api/auth/me': () => json(owner) })
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: /All projects/ }))
    await userEvent.click(await screen.findByRole('row', { name: 'Open Kestrel Life' }))

    expect(await screen.findByText('You have view-only access to this project’s forecast.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Revenue for Oct 2026')).not.toBeInTheDocument()
    expect(projects[1].can_edit).toBe(false)
  })

  it('launches the Revenue Detective from a flagged item', async () => {
    mockFetch()
    render(<App />)
    await openProject()
    await userEvent.click(screen.getByRole('button', { name: 'Open Revenue Detective' }))

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('AI Revenue Detective')).toBeInTheDocument()
    expect(within(dialog).getByText(/cannot change revenue or decide a finding/)).toBeInTheDocument()
  })

  it('explains that a Client Partner cannot re-run the checks, but lets leadership do it', async () => {
    mockFetch()
    const partner = render(<App />)
    await openProject()
    expect(screen.getByText(/Checks re-run every night/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Re-run checks' })).not.toBeInTheDocument()
    partner.unmount()

    vi.restoreAllMocks()
    mockFetch({ '/api/auth/me': () => json(croMe), '/api/v1/qa/health': () => json({ ...health, can_run: true }) })
    render(<App />)
    // leadership opens a project from the exceptions list on the overview
    const exceptions = (await screen.findByText('Risks and exceptions')).closest('section') as HTMLElement
    await userEvent.click(within(exceptions).getAllByRole('button', { name: 'Open Revenue Detective' })[2])
    await userEvent.click(await screen.findByRole('button', { name: 'Open Data Platform Co' }))
    expect(await screen.findByRole('button', { name: 'Re-run checks' })).toBeInTheDocument()
  })
})
