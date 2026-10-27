import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import {
  adminMe,
  deals,
  detailOf,
  draftDetail,
  forecast,
  json,
  mockApi,
  okRoutes,
  projects,
  signedIn,
  syncOk,
  type Routes,
} from '../test/fixtures'

type ChartRecord = { config: { data: { labels: string[]; datasets: { label: string; data: number[] }[] } }; destroyed: boolean }
const charts = vi.hoisted(() => [] as ChartRecord[])
vi.mock('chart.js', () => {
  class Chart {
    static register() {}
    config: ChartRecord['config']
    destroyed = false
    constructor(_canvas: unknown, config: ChartRecord['config']) {
      this.config = config
      charts.push(this as unknown as ChartRecord)
    }
    destroy() {
      this.destroyed = true
    }
  }
  return { Chart, CategoryScale: {}, LinearScale: {}, LineController: {}, LineElement: {}, PointElement: {}, Legend: {}, Tooltip: {} }
})

function mockFetch(overrides: Routes = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi({ ...okRoutes, ...overrides }))
}

function urls(method = 'GET') {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([url]) => String(url))
}

async function openPipeline() {
  render(<App />)
  await userEvent.click(await screen.findByRole('tab', { name: 'Pipeline' }))
  return screen.findByRole('table', { name: 'Deals' })
}

describe('Pipeline view', () => {
  beforeEach(() => {
    charts.length = 0
  })
  afterEach(() => vi.restoreAllMocks())

  it('has Forecast and Pipeline tabs and opens on the committed forecast', async () => {
    mockFetch()
    render(<App />)

    const forecastTab = await screen.findByRole('tab', { name: 'Forecast' })
    expect(forecastTab).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('Cedarfall Energy')).toBeInTheDocument()
    expect(urls().some((u) => u.includes('/pipeline/'))).toBe(false) // not loaded until opened

    await userEvent.click(screen.getByRole('tab', { name: 'Pipeline' }))
    expect(screen.getByRole('tab', { name: 'Pipeline' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('table', { name: 'Deals' })).toBeInTheDocument()
  })

  it('shows the totals exactly as the server calculated them', async () => {
    // Deliberately not the sum of its parts: the page must display, not recompute.
    mockFetch({ '/api/v1/forecast/monthly': () => json({ ...forecast, combined_total: '12345.00' }) })
    await openPipeline()

    const cards = within(screen.getByRole('region', { name: 'Forecast totals' }))
    expect(cards.getByText('$9,000')).toBeInTheDocument()
    expect(cards.getByText('$10,000')).toBeInTheDocument()
    expect(cards.getByText('$12,345')).toBeInTheDocument()
    expect(cards.getByText('2 deals')).toBeInTheDocument()
  })

  it('lays out committed, weighted and combined by month', async () => {
    mockFetch()
    await openPipeline()

    const table = screen.getByRole('table', { name: 'Monthly totals' })
    expect(within(table).getByRole('columnheader', { name: 'Nov 2026' })).toBeInTheDocument()
    const row = (name: string) => within(within(table).getByRole('row', { name: new RegExp(name) }))
    expect(row('Committed').getByText('$6,000')).toBeInTheDocument()
    expect(row('Weighted pipeline').getByText('$10,000')).toBeInTheDocument()
    expect(row('Combined').getByText('$16,000')).toBeInTheDocument()
  })

  it('says what is left out of the monthly figures instead of hiding it', async () => {
    mockFetch()
    await openPipeline()
    expect(screen.getByText(/1 deal\(s\) worth \$596,000 weighted have no start date or duration/)).toBeInTheDocument()
    expect(screen.getByText(/\$40,000 of weighted pipeline falls outside the months shown/)).toBeInTheDocument()
  })

  it('lists deals with stage, probability, amounts and status', async () => {
    mockFetch()
    const table = await openPipeline()

    const first = within(within(table).getByRole('row', { name: /Pembrook - Appian Renewal/ }))
    expect(first.getByText('Negotiation/Review')).toBeInTheDocument()
    expect(first.getByText('60%')).toBeInTheDocument()
    expect(first.getByText('$812,000')).toBeInTheDocument()
    expect(first.getByText('$487,200')).toBeInTheDocument()
    expect(first.getByText('Oct 2026')).toBeInTheDocument()
    expect(first.getByText('1 finding(s)')).toBeInTheDocument()
    const won = within(within(table).getByRole('row', { name: /Brightharbor/ }))
    expect(won.getByText('Awaiting confirmation')).toBeInTheDocument()
    expect(won.getByText('Overdue')).toBeInTheDocument()
    const unphased = within(within(table).getByRole('row', { name: /Brambleton/ }))
    expect(unphased.getByText('Not phased')).toBeInTheDocument()
  })

  it('applies every forecast filter to both the totals and the deal list', async () => {
    mockFetch()
    await openPipeline()
    for (const [label, value] of [
      ['Owner', 'Shah'], ['TECH', 'CRM'], ['Region', 'UK'], ['Industry', 'Insurance'],
      ['Category', 'EN'], ['Customer', 'Pembrook Housing Group'],
    ] as const) {
      await userEvent.selectOptions(screen.getByLabelText(label), value)
    }

    await waitFor(() => {
      const last = (path: string) => urls().filter((u) => u.includes(path)).at(-1) ?? ''
      for (const path of ['/v1/forecast/monthly', '/v1/pipeline/deals']) {
        const params = new URL(last(path), 'http://x').searchParams
        expect(Object.fromEntries(params)).toMatchObject({
          owner: 'Shah', practice: 'CRM', region: 'UK', industry: 'Insurance',
          category: 'EN', customer: 'Pembrook Housing Group',
        })
      }
    })
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(urls().at(-1)).not.toContain('owner='))
  })

  it('searches deals after a pause in typing', async () => {
    mockFetch()
    await openPipeline()
    await userEvent.type(screen.getByLabelText('Search deals'), 'Kestrel')
    await waitFor(() => expect(urls().some((u) => u.includes('/pipeline/deals') && u.includes('q=Kestrel'))).toBe(true))
  })

  it('switches between deal views, asking the server for each', async () => {
    mockFetch()
    await openPipeline()
    expect(urls().some((u) => u.includes('/pipeline/deals') && u.includes('status=counting'))).toBe(true)
    await userEvent.selectOptions(screen.getByLabelText('Deal view'), 'closed_lost')
    await waitFor(() => expect(urls().at(-1)).toContain('status=closed_lost'))
  })

  it('draws the chart from the API figures, only once opened, and cleans up', async () => {
    mockFetch()
    await openPipeline()
    expect(charts).toHaveLength(0) // collapsed below the numbers

    await userEvent.click(screen.getByText('Chart: committed, weighted and combined by month'))
    await waitFor(() => expect(charts).toHaveLength(1))
    const { labels, datasets } = charts[0].config.data
    expect(labels).toEqual(['Oct 2026', 'Nov 2026'])
    expect(datasets.map((d) => [d.label, d.data])).toEqual([
      ['Committed', [3000, 6000]],
      ['Weighted pipeline', [0, 10000]],
      ['Combined', [3000, 16000]],
    ])
    await userEvent.click(screen.getByText('Chart: committed, weighted and combined by month'))
    await waitFor(() => expect(charts[0].destroyed).toBe(true))
  })
})

describe('Deal details', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the deal, its phasing, findings and history', async () => {
    mockFetch()
    const table = await openPipeline()
    await userEvent.click(within(table).getByRole('button', { name: 'Pembrook - Appian Renewal' }))

    const panel = await screen.findByRole('complementary', { name: /Details for deal Pembrook/ })
    expect(await within(panel).findByText('Nov 2026')).toBeInTheDocument()
    expect(within(panel).getByText('$812,000.00')).toBeInTheDocument()
    expect(within(panel).getByText('$487,200.00')).toBeInTheDocument()
    expect(within(panel).getByText('Negotiation/Review (60%)')).toBeInTheDocument()
    expect(within(panel).getByText('Open deal with a close date in the past.')).toBeInTheDocument()
    expect(within(panel).getByText('Draft project created')).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: /Confirm schedule/ })).not.toBeInTheDocument()
  })

  it('explains a deal that cannot be phased rather than guessing', async () => {
    mockFetch({
      '/api/v1/pipeline/deals/3': () => json(detailOf(deals[2], { monthly: [], findings_list: [] })),
    })
    const table = await openPipeline()
    await userEvent.click(within(table).getByRole('button', { name: /Brambleton/ }))
    const panel = await screen.findByRole('complementary', { name: /Details for deal Brambleton/ })
    expect(await within(panel).findByText(/Not phased by month/)).toBeInTheDocument()
  })

  it('reports a deal that cannot be loaded', async () => {
    mockFetch({ '/api/v1/pipeline/deals/1': () => json({ detail: 'boom' }, 500) })
    const table = await openPipeline()
    await userEvent.click(within(table).getByRole('button', { name: 'Pembrook - Appian Renewal' }))
    expect(await screen.findByText('Could not load this deal.')).toBeInTheDocument()
  })

  it('jumps to the draft project in the Forecast tab to edit its schedule', async () => {
    const draftProject = { ...projects[0], id: 55, name: 'Brightharbor - Claims AI Pilot', status: 'Draft' }
    mockFetch({
      '/api/v1/projects': () =>
        json({ items: [...projects, draftProject], page: 1, page_size: 500, total_items: 3, total_pages: 1 }),
      '/api/v1/projects/55': () => json({ ...draftDetail, id: 55, owners: [], monthly_revenue: [], attention: [], can_view_history: false, source_row_number: null, last_updated_by: null }),
    })
    const table = await openPipeline()
    await userEvent.click(within(table).getByRole('button', { name: /Brightharbor/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Open the project to edit its monthly schedule/ }))

    expect(await screen.findByRole('tab', { name: 'Forecast' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('complementary', { name: /Details for Brightharbor/ })).toBeInTheDocument()
    await waitFor(() => expect(urls().some((u) => u.endsWith('/v1/projects/55'))).toBe(true))
  })
})

describe('Confirming a draft project', () => {
  afterEach(() => vi.restoreAllMocks())

  async function openDraft() {
    const table = await openPipeline()
    await userEvent.click(within(table).getByRole('button', { name: /Brightharbor/ }))
    return screen.findByRole('complementary', { name: /Details for deal Brightharbor/ })
  }

  it('lists drafts awaiting confirmation, marking overdue ones, and opens them for review', async () => {
    mockFetch()
    await openPipeline()
    const list = screen.getByRole('region', { name: 'Awaiting confirmation' })
    expect(within(list).getByText('Awaiting confirmation (1)')).toBeInTheDocument()
    expect(within(list).getByText('Overdue')).toBeInTheDocument()
    await userEvent.click(within(list).getByRole('button', { name: 'Review and confirm' }))
    expect(await screen.findByRole('button', { name: 'Confirm schedule and commit' })).toBeInTheDocument()
  })

  it('confirms with the CSRF token and the chosen details, then refreshes from the server', async () => {
    let confirmed: { body: unknown; headers: Record<string, string> } | null = null
    mockFetch({
      'POST /api/v1/pipeline/drafts/55/confirm': (request) => {
        confirmed = request
        return json({ project_id: 55, status: 'Active', weighted_line_retired: true, schedule_check: null })
      },
    })
    const panel = await openDraft()
    expect(await within(panel).findByText(/confirmation due/)).toBeInTheDocument()
    await userEvent.selectOptions(within(panel).getByLabelText('Category'), 'NN')
    await userEvent.type(within(panel).getByLabelText('Industry'), 'Insurance')
    const before = urls().filter((u) => u.includes('/v1/forecast/monthly')).length

    await userEvent.click(within(panel).getByRole('button', { name: 'Confirm schedule and commit' }))

    expect(await within(panel).findByText(/The project is now committed and the weighted line has been retired/)).toBeInTheDocument()
    expect(confirmed).toEqual({
      body: { acknowledge_possible_duplicate: false, category: 'NN', industry: 'Insurance' },
      headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-token-123' }),
    })
    await waitFor(() => expect(urls().filter((u) => u.includes('/v1/forecast/monthly')).length).toBeGreaterThan(before))
  })

  it('requires acknowledging a possible duplicate before the button works', async () => {
    const withDuplicate = {
      ...deals[1],
      draft: { ...deals[1].draft!, possible_duplicates: [{ project_id: 7, source_row_id: 'R3', name: 'Existing Work', status: 'Active' }] },
    }
    let acknowledged: unknown
    mockFetch({
      '/api/v1/pipeline/deals/2': () => json({ ...draftDetail, ...withDuplicate, can_confirm: true }),
      'POST /api/v1/pipeline/drafts/55/confirm': ({ body }) => {
        acknowledged = (body as { acknowledge_possible_duplicate: boolean }).acknowledge_possible_duplicate
        return json({ project_id: 55, status: 'Active', weighted_line_retired: true, schedule_check: null })
      },
    })
    const panel = await openDraft()

    const button = await within(panel).findByRole('button', { name: 'Confirm schedule and commit' })
    expect(within(panel).getByText('Existing Work')).toBeInTheDocument()
    expect(button).toBeDisabled()
    await userEvent.click(within(panel).getByLabelText('This is new work, not a duplicate'))
    expect(button).toBeEnabled()
    await userEvent.click(button)
    await waitFor(() => expect(acknowledged).toBe(true))
  })

  it('shows the server refusal and leaves the draft as it was', async () => {
    mockFetch({
      'POST /api/v1/pipeline/drafts/55/confirm': () =>
        json({ detail: { code: 'empty_schedule', message: 'Enter the monthly revenue schedule before confirming.' } }, 422),
    })
    const panel = await openDraft()
    await userEvent.click(await within(panel).findByRole('button', { name: 'Confirm schedule and commit' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent('Enter the monthly revenue schedule before confirming.')
    expect(within(panel).queryByText(/now committed/)).not.toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Confirm schedule and commit' })).toBeEnabled()
  })

  it('shows an authorization failure naming the owner', async () => {
    mockFetch({
      'POST /api/v1/pipeline/drafts/55/confirm': () =>
        json({ detail: { code: 'not_project_owner', message: 'You cannot edit it: it is owned by Riley Shah (Account Executive).' } }, 403),
    })
    const panel = await openDraft()
    await userEvent.click(await within(panel).findByRole('button', { name: 'Confirm schedule and commit' }))
    expect(await within(panel).findByRole('alert')).toHaveTextContent('owned by Riley Shah')
  })

  it('offers no confirm button to people who may not confirm', async () => {
    mockFetch({ '/api/v1/pipeline/deals/2': () => json({ ...draftDetail, can_confirm: false }) })
    const panel = await openDraft()
    expect(await within(panel).findByText(/Only this project’s owners, Sales leadership or an Administrator can confirm it/)).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Confirm schedule and commit' })).not.toBeInTheDocument()
  })

  it('flags a schedule that differs from the deal by more than 10%', async () => {
    mockFetch({
      '/api/v1/pipeline/deals/2': () =>
        json({
          ...draftDetail,
          schedule_check: { ...draftDetail.schedule_check!, schedule_total: '18600.00', variance_ratio: '0.9000', exceeds_threshold: true },
        }),
    })
    const panel = await openDraft()
    expect(await within(panel).findByText(/differs from the deal by more than 10%/)).toBeInTheDocument()
  })

  it('returns to sign-in if the session ended while confirming', async () => {
    mockFetch({
      'POST /api/v1/pipeline/drafts/55/confirm': () => json({ detail: { code: 'not_authenticated', message: 'Sign in.' } }, 401),
    })
    const panel = await openDraft()
    await userEvent.click(await within(panel).findByRole('button', { name: 'Confirm schedule and commit' }))
    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument()
  })
})

describe('Sync status and failures', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows when data was last synced', async () => {
    mockFetch()
    await openPipeline()
    expect(await screen.findByText(/Last synced .* 53 deals/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument() // not permitted
  })

  it('warns that the sync is stale after a failure, with the reason, and keeps showing data', async () => {
    mockFetch({
      '/api/v1/pipeline/sync/status': () =>
        json({
          ...syncOk, stale: true, reason: 'failed', stale_since: '2026-10-01T09:00:05Z',
          last_run: { ...syncOk.last_success!, status: 'failed', error_message: 'HubSpot is unreachable' },
        }),
    })
    const table = await openPipeline()
    const banner = await screen.findByText(/Sync stale since/)
    expect(banner).toHaveTextContent('the latest sync failed (HubSpot is unreachable). Showing the last good data.')
    expect(within(table).getByText('Pembrook - Appian Renewal')).toBeInTheDocument()
  })

  it.each([
    ['never', /No HubSpot sync has run yet/],
    ['old', /older than 60 minutes/],
  ])('explains a %s sync', async (reason, text) => {
    mockFetch({ '/api/v1/pipeline/sync/status': () => json({ ...syncOk, stale: true, reason, last_success: reason === 'never' ? null : syncOk.last_success }) })
    await openPipeline()
    expect(await screen.findByText(text)).toBeInTheDocument()
  })

  it('lets a permitted user sync now and refreshes everything', async () => {
    let status = { ...syncOk, can_sync: true }
    mockFetch({
      '/api/v1/pipeline/sync/status': () => json(status),
      'POST /api/v1/pipeline/sync': () => {
        status = { ...status, last_success: { ...status.last_success!, deals_seen: 54 } }
        return json({ ...syncOk.last_success, deals_seen: 54, deals_created: 1, deals_updated: 0, drafts_created: 1 })
      },
    })
    await openPipeline()
    await userEvent.click(await screen.findByRole('button', { name: 'Sync now' }))

    expect(await screen.findByText('Sync complete: 54 deals (1 new, 0 updated), 1 draft project(s).')).toBeInTheDocument()
    expect(await screen.findByText(/Last synced .* 54 deals/)).toBeInTheDocument()
    expect(vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST' && String(init?.headers && JSON.stringify(init.headers)).includes('csrf-token-123'))).toBeDefined()
  })

  it('shows a failed sync without losing the forecast', async () => {
    mockFetch({
      '/api/v1/pipeline/sync/status': () => json({ ...syncOk, can_sync: true }),
      'POST /api/v1/pipeline/sync': () =>
        json({ detail: { code: 'sync_failed', message: 'The HubSpot sync failed: HubSpot unreachable. The forecast is unchanged.' } }, 502),
    })
    const table = await openPipeline()
    await userEvent.click(await screen.findByRole('button', { name: 'Sync now' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The forecast is unchanged.')
    expect(within(table).getByText('Pembrook - Appian Renewal')).toBeInTheDocument()
  })

  it('still shows the pipeline if the sync status cannot be read', async () => {
    mockFetch({ '/api/v1/pipeline/sync/status': () => json({ detail: 'boom' }, 500) })
    const table = await openPipeline()
    expect(await screen.findByText(/Could not check the HubSpot sync status/)).toBeInTheDocument()
    expect(within(table).getByText('Pembrook - Appian Renewal')).toBeInTheDocument()
  })
})

describe('Pipeline states and errors', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows a loading state while the pipeline loads', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      if (String(input).includes('/v1/forecast/monthly')) return new Promise<Response>(() => undefined)
      return mockApi(okRoutes)(input, init)
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Pipeline' }))
    expect(await screen.findByText('Loading the pipeline…')).toBeInTheDocument()
  })

  it('shows an error with retry, and the committed forecast keeps working', async () => {
    let failing = true
    mockFetch({ '/api/v1/forecast/monthly': () => (failing ? json({ detail: 'boom' }, 500) : json(forecast)) })
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Pipeline' }))
    expect(await screen.findByText('The pipeline could not be loaded.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: 'Forecast' }))
    expect(await screen.findByText('Cedarfall Energy')).toBeInTheDocument() // unaffected

    await userEvent.click(screen.getByRole('tab', { name: 'Pipeline' }))
    failing = false
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('table', { name: 'Deals' })).toBeInTheDocument()
  })

  it('shows an empty state that points to the sync', async () => {
    mockFetch({
      '/api/v1/pipeline/deals': () => json({ items: [], page: 1, page_size: 500, total_items: 0, total_pages: 0, weighted_total: '0.00' }),
      '/api/v1/pipeline/confirmations': () => json([]),
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Pipeline' }))
    expect(await screen.findByText('Run a HubSpot sync to bring deals in.')).toBeInTheDocument()
  })

  it('returns to sign-in when the session ends', async () => {
    mockFetch({ '/api/v1/forecast/monthly': () => json({ detail: { code: 'not_authenticated', message: 'Sign in.' } }, 401) })
    render(<App />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Pipeline' }))
    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument()
  })
})

describe('Stage probabilities', () => {
  afterEach(() => vi.restoreAllMocks())

  it('are visible to everyone but editable only by Administrators', async () => {
    mockFetch()
    await openPipeline()
    await userEvent.click(screen.getByText('Stage probabilities'))
    const panel = screen.getByText('Stage probabilities').closest('details') as HTMLElement
    expect(await within(panel).findByText('60%')).toBeInTheDocument()
    expect(screen.queryByLabelText('Probability for Negotiation/Review')).not.toBeInTheDocument()
    expect(screen.getByText(/Only an Administrator can change these/)).toBeInTheDocument()
  })

  it('lets an Administrator change one, with the CSRF token, and re-reads the totals', async () => {
    let put: { body: unknown; headers: Record<string, string> } | null = null
    mockFetch({
      '/api/auth/me': () => json(adminMe),
      'PUT /api/v1/pipeline/stages/2': (request) => {
        put = request
        return json({ id: 2, name: 'Negotiation/Review', probability: '0.7500', kind: 'open', in_forecast: true, position: 1, source: 'workbook' })
      },
    })
    await openPipeline()
    await userEvent.click(screen.getByText('Stage probabilities'))
    const input = await screen.findByLabelText('Probability for Negotiation/Review')
    const before = urls().filter((u) => u.includes('/v1/forecast/monthly')).length
    await userEvent.clear(input)
    await userEvent.type(input, '0.75{Enter}')

    expect(await screen.findByText('Saved Negotiation/Review: 75%.')).toBeInTheDocument()
    expect(put).toEqual({ body: { probability: '0.75' }, headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-token-123' }) })
    await waitFor(() => expect(urls().filter((u) => u.includes('/v1/forecast/monthly')).length).toBeGreaterThan(before))
  })

  it('shows the server message when a probability is refused', async () => {
    mockFetch({
      '/api/auth/me': () => json(adminMe),
      'PUT /api/v1/pipeline/stages/2': () => json({ detail: { code: 'invalid_probability', message: 'Probability must be between 0 and 1.' } }, 422),
    })
    await openPipeline()
    await userEvent.click(screen.getByText('Stage probabilities'))
    const input = await screen.findByLabelText('Probability for Negotiation/Review')
    await userEvent.clear(input)
    await userEvent.type(input, '7{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('Probability must be between 0 and 1.')
  })
})

describe('Existing views are unchanged', () => {
  afterEach(() => vi.restoreAllMocks())

  it('keeps the signed-in shell and a working grid', async () => {
    mockFetch({ '/api/auth/me': () => json(signedIn) })
    render(<App />)
    expect(await screen.findByText('Avery Chen')).toBeInTheDocument()
    expect(await screen.findByText('Cedarfall Energy')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Forecast' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })
})
