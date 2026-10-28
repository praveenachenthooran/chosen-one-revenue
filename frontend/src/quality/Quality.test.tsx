import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import {
  completedInvestigation,
  croMe,
  finding1,
  findingDetail,
  health,
  json,
  mockApi,
  okRoutes,
  type Routes,
} from '../test/fixtures'

vi.mock('chart.js', () => {
  class Chart {
    static register() {}
    destroy() {}
  }
  return { Chart, CategoryScale: {}, LinearScale: {}, LineController: {}, LineElement: {}, PointElement: {}, Legend: {}, Tooltip: {}, BarController: {}, BarElement: {}, Filler: {} }
})

function mockFetch(overrides: Routes = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi({ ...okRoutes, ...overrides }))
}

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown }
function calls(method = 'GET'): Call[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([url, init]) => ({
      url: String(url),
      method: (init?.method ?? 'GET').toUpperCase(),
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : init?.body,
    }))
    .filter((c) => c.method === method)
}

async function openQuality() {
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: 'Checks' }))
  return screen.findByRole('table', { name: 'Findings' })
}

async function openFinding(title: RegExp) {
  await userEvent.click(await screen.findByRole('button', { name: title }))
  return screen.findByRole('dialog')
}

const LEADER: Routes = { '/api/auth/me': () => json(croMe) }
const CAN_RUN: Routes = { '/api/v1/qa/health': () => json({ ...health, can_run: true }) }

afterEach(() => vi.restoreAllMocks())

describe('Quality tab and Forecast Health', () => {
  it('is available to every signed-in role and does not load until opened', async () => {
    mockFetch()
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Checks' })).toBeInTheDocument()
    await screen.findByRole('row', { name: 'Open Cedarfall Energy' })
    // The findings workspace itself opens on demand; only the status summary loads up front.
    expect(screen.queryByRole('table', { name: 'Findings' })).not.toBeInTheDocument()
    expect(calls().some((c) => c.url.includes('/qa/investigations') || c.url.includes('/qa/runs'))).toBe(false)
  })

  it('shows health as plain counts with the reasons for its status', async () => {
    mockFetch()
    await openQuality()
    const region = screen.getByRole('region', { name: 'Forecast health' })
    expect(within(region).getByRole('status')).toHaveTextContent('Forecast health: Action needed')
    expect(within(region).getByRole('status')).toHaveTextContent('1 open error finding(s). 2 open warning(s).')
    const cards = within(within(region).getByLabelText('Health totals'))
    expect(cards.getByText('3')).toBeInTheDocument()
    expect(cards.getByText('$327,000')).toBeInTheDocument()
    expect(cards.getByText('1 errors · 2 warnings')).toBeInTheDocument()
    expect(within(region).getByText(/Last check .* \(nightly, completed\)/)).toBeInTheDocument()
    expect(within(region).getByText('How these numbers are calculated')).toBeInTheDocument()
    expect(within(region).getByText(/There is no hidden score/)).toBeInTheDocument()
  })

  it('lists the owners who need to act, including the unassigned work', async () => {
    mockFetch()
    await openQuality()
    const owners = within(screen.getByRole('table', { name: 'Owners needing attention' }))
    expect(owners.getByRole('button', { name: 'Avery Chen' })).toBeInTheDocument()
    expect(owners.getByText('Unassigned (needs an owner)')).toBeInTheDocument()
    expect(owners.getByText('$207,000')).toBeInTheDocument()
    expect(owners.getByText('3 d')).toBeInTheDocument()

    await userEvent.click(owners.getByRole('button', { name: 'Avery Chen' }))
    const rows = within(screen.getByRole('table', { name: 'Findings' })).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2) // only Avery's findings
    await userEvent.click(screen.getByRole('button', { name: 'Clear owner filter' }))
    expect(within(screen.getByRole('table', { name: 'Findings' })).getAllByRole('row').slice(1)).toHaveLength(3)
  })

  it('offers no "Run checks now" to people who may not run the checks', async () => {
    mockFetch()
    await openQuality()
    expect(screen.queryByRole('button', { name: 'Run checks now' })).not.toBeInTheDocument()
  })

  it('lets leadership run the checks and shows the outcome', async () => {
    mockFetch({
      ...LEADER,
      ...CAN_RUN,
      'POST /api/v1/qa/runs': () => json({ ...health.last_run, id: 9, findings_new: 2, findings_reopened: 0, findings_resolved: 1, findings_open: 3 }),
    })
    await openQuality()
    await userEvent.click(await screen.findByRole('button', { name: 'Run checks now' }))
    expect(await screen.findByText('Checks complete: 2 new, 0 reopened, 1 resolved, 3 open.')).toBeInTheDocument()
    expect(calls('POST')[0].headers['X-CSRF-Token']).toBe('csrf-token-123')
  })

  it('says so when a run could not start, and keeps the findings', async () => {
    mockFetch({
      ...CAN_RUN,
      'POST /api/v1/qa/runs': () => json({ detail: { code: 'qa_busy', message: 'A QA run is already in progress.' } }, 409),
    })
    await openQuality()
    await userEvent.click(screen.getByRole('button', { name: 'Run checks now' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A QA run is already in progress.')
    expect(screen.getByRole('table', { name: 'Findings' })).toBeInTheDocument()
  })

  it('shows overdue and failed checks in the banner', async () => {
    mockFetch({
      '/api/v1/qa/health': () =>
        json({ ...health, status: 'amber', status_reasons: ['No successful QA run in the last 36 hours.'], last_run: { ...health.last_run, status: 'failed', error_message: 'RuntimeError: boom' } }),
    })
    await openQuality()
    const banner = screen.getByRole('region', { name: 'Forecast health' })
    expect(within(banner).getByRole('status')).toHaveTextContent('Needs attention')
    expect(within(banner).getByText(/failed\): RuntimeError: boom/)).toBeInTheDocument()
  })
})

describe('Findings list', () => {
  it('shows severity, the check, who it is routed to, the impact and its state', async () => {
    mockFetch()
    const table = await openQuality()
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    const missing = within(rows[0])
    expect(missing.getByText('error')).toBeInTheDocument()
    expect(missing.getByText('Unassigned')).toBeInTheDocument()
    expect(missing.getByText('$120,000')).toBeInTheDocument()
    expect(missing.getByText('Missing required fields')).toBeInTheDocument()
    const anomaly = within(rows[1])
    expect(anomaly.getByText('Avery Chen')).toBeInTheDocument()
    expect(anomaly.getByText('Open')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Awaiting review')).toBeInTheDocument() // investigated, needs a decision
  })

  it('asks the server for the chosen filters', async () => {
    mockFetch()
    await openQuality()
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'dismissed')
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'error')
    await userEvent.selectOptions(screen.getByLabelText('Check'), 'stale_row')
    await userEvent.click(screen.getByLabelText('Routed to me'))
    await waitFor(() =>
      expect(calls().some((c) => c.url.includes('/qa/findings?') && c.url.includes('status=dismissed') && c.url.includes('severity=error') && c.url.includes('rule=stale_row') && c.url.includes('mine=true'))).toBe(true),
    )
  })

  it('has an empty state and an error state that leaves the forecast alone', async () => {
    mockFetch({ '/api/v1/qa/findings': () => json({ items: [], total_items: 0 }) })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Checks' }))
    expect(await screen.findByText('No findings match.')).toBeInTheDocument()

    vi.restoreAllMocks()
    mockFetch({ '/api/v1/qa/findings': () => json({ detail: 'boom' }, 500) })
    await userEvent.click(screen.getByRole('button', { name: 'Monthly grid' }))
    await userEvent.click(screen.getByRole('button', { name: 'Checks' }))
    expect(await screen.findByText('Findings could not be loaded.')).toBeInTheDocument()
    expect(screen.getByText('The forecast itself is unaffected.')).toBeInTheDocument()
  })
})

describe('Finding panel', () => {
  it('shows the rule, evidence, suggested fix, affected project and audit trail', async () => {
    mockFetch()
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))

    expect(panel.getByText(/with no reason recorded/)).toBeInTheDocument()
    expect(panel.getByText('$21,000')).toBeInTheDocument()
    expect(panel.getByText(/with Avery Chen/)).toBeInTheDocument()
    expect(panel.getByText(/Ask the owner what changed/)).toBeInTheDocument()
    await userEvent.click(panel.getByText('View evidence'))
    expect(panel.getByText('Billing type')).toBeInTheDocument()
    const trail = within(panel.getByText('History').closest('details') as HTMLElement)
    expect(trail.getByText(/Raised by the mom_anomaly check/)).toBeInTheDocument()
    expect(trail.getByText(/qa-engine/)).toBeInTheDocument()
  })

  it('opens the underlying project, with its history, on the project page', async () => {
    mockFetch()
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    await userEvent.click(panel.getByRole('button', { name: 'Open Data Platform Co' }))

    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    expect(await screen.findByText('Change history')).toBeInTheDocument()
  })

  it('opens the underlying HubSpot deal in the Pipeline tab', async () => {
    mockFetch()
    await openQuality()
    await openFinding(/Closed Won not committed/)
    await userEvent.click(screen.getByRole('button', { name: 'Open HubSpot deal' }))

    expect(screen.getByRole('button', { name: 'Pipeline' })).toHaveAttribute('aria-current', 'page')
    expect(await screen.findByRole('complementary', { name: /Pembrook - Appian Renewal/ })).toBeInTheDocument()
  })

  it('is explicit that the detective is read-only and says why Claude is not in use', async () => {
    mockFetch()
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    const section = within(panel.getByRole('region', { name: 'Investigation' }))
    expect(section.getByText(/cannot change revenue or decide a finding/)).toBeInTheDocument()
    expect(section.getByText(/Claude is not in use \(No Anthropic API key is configured\.\)/)).toBeInTheDocument()
    expect(section.getByText('Not investigated yet.')).toBeInTheDocument()
  })

  it('walks through investigating then completed, polling until the result arrives', async () => {
    let polls = 0
    const running = { ...completedInvestigation, status: 'investigating' as const, result: null, steps: [], finished_at: null }
    mockFetch({
      'POST /api/v1/qa/findings/11/investigate': () => json(running, 202),
      '/api/v1/qa/findings/11': () => {
        const started = calls('POST').some((c) => c.url.endsWith('/findings/11/investigate'))
        polls += started ? 1 : 0
        const investigation = !started ? undefined : polls < 2 ? running : completedInvestigation
        return json({ ...findingDetail, investigations: investigation ? [investigation] : [], awaiting_review: investigation?.status === 'completed', investigation_status: investigation?.status ?? null })
      },
    })
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    await userEvent.click(panel.getByRole('button', { name: 'Investigate' }))

    expect(await panel.findByText(/Investigating… looking through/)).toBeInTheDocument()
    expect(panel.queryByRole('button', { name: 'Investigate' })).not.toBeInTheDocument()
    expect(await panel.findByText(/unexplained 70\.0% movement/, {}, { timeout: 6000 })).toBeInTheDocument()
    expect(panel.getByText('Awaiting review')).toBeInTheDocument()
    expect(calls('POST')[0].headers['X-CSRF-Token']).toBe('csrf-token-123')
  }, 15000)

  it('distinguishes confirmed causes, hypotheses and insufficient evidence, with their evidence', async () => {
    mockFetch({ '/api/v1/qa/findings/11': () => json({ ...findingDetail, investigations: [completedInvestigation], awaiting_review: true, investigation_status: 'completed' }) })
    await openQuality()
    const section = within(within(await openFinding(/Unusual revenue rise/)).getByRole('region', { name: 'Investigation' }))

    expect(section.getByText('Rule-based checks')).toBeInTheDocument()
    expect(section.getByText('medium confidence')).toBeInTheDocument()
    expect(section.getByText('Confirmed')).toBeInTheDocument()
    expect(section.getByText('Hypothesis')).toBeInTheDocument()
    expect(section.getByText('Insufficient evidence')).toBeInTheDocument()
    expect(section.getByText('deal:9, project:1')).toBeInTheDocument() // the sources, kept behind "Sources"
    expect(section.getByText('project:1')).toBeInTheDocument()
    expect(section.getByText(/Closed Lost deal exists for the same customer; nothing links it/)).toBeInTheDocument()
    expect(section.getByText('$21,000')).toBeInTheDocument()
    expect(section.getByText(/the size of the largest month-over-month movement/)).toBeInTheDocument()
    expect(section.getByText(/not estimated by the model/)).toBeInTheDocument()
    expect(section.getByText(/Ask the row owner what changed/)).toBeInTheDocument()
    expect(section.getAllByText(/No Anthropic API key is configured\./)).toHaveLength(2) // why, and the fallback note
    expect(section.getByText('What this can’t tell us')).toBeInTheDocument()
    expect(section.getByText('Fewer than two snapshots exist.')).toBeInTheDocument()
    // How it was checked is in plain words on request, never as tool calls or JSON.
    await userEvent.click(within(within(await screen.findByRole('dialog')).getByText('View evidence').closest('details') as HTMLElement).getByText('View evidence'))
    expect(screen.getByText(/Read the project’s change history: ok/)).toBeInTheDocument()
    expect(screen.queryByText(/"project_id"/)).not.toBeInTheDocument()
  })

  it('shortens a long summary to two sentences, keeps decimals intact, and expands on request', async () => {
    const long = {
      ...completedInvestigation,
      result: {
        ...completedInvestigation.result!,
        summary: 'Revenue rose 74.4% between January and February. The rise is confirmed by the records. A related deal may explain it. Nothing proves that link.',
      },
    }
    mockFetch({ '/api/v1/qa/findings/11': () => json({ ...findingDetail, investigations: [long], awaiting_review: true, investigation_status: 'completed' }) })
    await openQuality()
    const section = within(within(await openFinding(/Unusual revenue rise/)).getByRole('region', { name: 'Investigation' }))

    expect(section.getByText(/Revenue rose 74\.4% between January and February\. The rise is confirmed by the records\./)).toBeInTheDocument()
    expect(section.queryByText(/Nothing proves that link/)).not.toBeInTheDocument()
    await userEvent.click(section.getByRole('button', { name: 'Read more' }))
    expect(section.getByText(/Nothing proves that link/)).toBeInTheDocument()
    await userEvent.click(section.getByRole('button', { name: 'Show less' }))
    expect(section.queryByText(/Nothing proves that link/)).not.toBeInTheDocument()
  })

  it('labels an investigation done by Claude', async () => {
    const ai = { ...completedInvestigation, mode: 'ai' as const, model: 'claude-sonnet-5-5', fallback_reason: null }
    mockFetch({ '/api/v1/qa/findings/11': () => json({ ...findingDetail, investigations: [ai] }) })
    await openQuality()
    const section = within(within(await openFinding(/Unusual revenue rise/)).getByRole('region', { name: 'Investigation' }))
    expect(section.getByText('Claude')).toBeInTheDocument()
  })

  it('shows a failed investigation with the reason and a way to retry', async () => {
    const failed = { ...completedInvestigation, status: 'failed' as const, result: null, error_message: 'The investigation was interrupted before it finished. Run it again.' }
    mockFetch({ '/api/v1/qa/findings/11': () => json({ ...findingDetail, investigations: [failed], investigation_status: 'failed' }) })
    await openQuality()
    const section = within(within(await openFinding(/Unusual revenue rise/)).getByRole('region', { name: 'Investigation' }))
    expect(section.getByRole('alert')).toHaveTextContent('The investigation failed: The investigation was interrupted')
    expect(section.getByRole('button', { name: 'Investigate again' })).toBeInTheDocument()
  })

  it('does not offer an investigation to someone who may not run one', async () => {
    mockFetch({ '/api/v1/qa/findings/11': () => json({ ...findingDetail, can_investigate: false }) })
    await openQuality()
    const section = within(within(await openFinding(/Unusual revenue rise/)).getByRole('region', { name: 'Investigation' }))
    expect(section.queryByRole('button', { name: /Investigate/ })).not.toBeInTheDocument()
  })
})

describe('Human review', () => {
  it('needs a reason, then records an accept without changing any revenue', async () => {
    mockFetch({
      'POST /api/v1/qa/findings/11/accept': () => json({ ...finding1, status: 'accepted', review_reason: 'Real: will fix', reviewed_by_name: 'Avery Chen', can_review: false }),
    })
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    const accept = panel.getByRole('button', { name: 'Accept finding' })
    const dismiss = panel.getByRole('button', { name: 'Dismiss finding' })
    expect(accept).toBeDisabled()
    expect(dismiss).toBeDisabled()

    await userEvent.type(panel.getByLabelText('Reason for your decision (required)'), 'Real: will fix')
    expect(accept).toBeEnabled()
    await userEvent.click(accept)

    expect(await panel.findByText('Finding accepted. No revenue was changed.')).toBeInTheDocument()
    const [post] = calls('POST')
    expect(post.url).toBe('/api/v1/qa/findings/11/accept')
    expect(post.body).toEqual({ reason: 'Real: will fix' })
    expect(post.headers['X-CSRF-Token']).toBe('csrf-token-123')
    expect(calls('PUT')).toHaveLength(0) // nothing edits revenue
    expect(panel.getByText(/Accepting records that the finding is real/)).toBeInTheDocument()
  })

  it('dismisses with a reason', async () => {
    mockFetch({ 'POST /api/v1/qa/findings/11/dismiss': () => json({ ...finding1, status: 'dismissed' }) })
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    await userEvent.type(panel.getByLabelText('Reason for your decision (required)'), 'Intentional ramp')
    await userEvent.click(panel.getByRole('button', { name: 'Dismiss finding' }))
    expect(await panel.findByText('Finding dismissed.')).toBeInTheDocument()
    expect(calls('POST')[0].body).toEqual({ reason: 'Intentional ramp' })
  })

  it('shows the server’s refusal, naming the owner', async () => {
    mockFetch({
      'POST /api/v1/qa/findings/11/accept': () =>
        json({ detail: { code: 'not_finding_owner', message: 'Only the owner this finding is routed to (Avery Chen) can decide it.' } }, 403),
    })
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    await userEvent.type(panel.getByLabelText('Reason for your decision (required)'), 'I think so')
    await userEvent.click(panel.getByRole('button', { name: 'Accept finding' }))
    expect(await panel.findByRole('alert')).toHaveTextContent('Only the owner this finding is routed to (Avery Chen) can decide it.')
  })

  it('tells someone who cannot decide who can', async () => {
    mockFetch({ '/api/v1/qa/findings/11': () => json({ ...findingDetail, can_review: false }) })
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    expect(panel.queryByRole('button', { name: 'Accept finding' })).not.toBeInTheDocument()
    expect(panel.getByText(/Only Avery Chen, someone who can edit the affected project, Sales leadership or an Administrator can decide/)).toBeInTheDocument()
  })

  it('shows who decided a finding and why', async () => {
    mockFetch({
      '/api/v1/qa/findings/11': () =>
        json({ ...findingDetail, status: 'dismissed', review_reason: 'Known and accepted', reviewed_by_name: 'Casey CRO', reviewed_at: '2026-10-28T09:00:00Z', can_review: false }),
    })
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    expect(panel.getByText(/Dismissed by Casey CRO on .*: “Known and accepted”/)).toBeInTheDocument()
    expect(panel.queryByLabelText('Reason for your decision (required)')).not.toBeInTheDocument()
  })

  it('explains a finding that resolved itself', async () => {
    mockFetch({ '/api/v1/qa/findings/11': () => json({ ...findingDetail, status: 'resolved', reviewed_by_name: null, review_reason: null, can_review: false }) })
    await openQuality()
    const panel = within(await openFinding(/Unusual revenue rise/))
    expect(panel.getByText(/Resolved automatically: the condition is no longer detected\./)).toBeInTheDocument()
  })
})
