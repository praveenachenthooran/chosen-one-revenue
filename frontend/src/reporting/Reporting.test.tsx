import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import {
  accuracy,
  actualMonths,
  croMe,
  financeMe,
  history,
  json,
  mockApi,
  okRoutes,
  reconciliation,
  snapshotList,
  snapshotTotals,
  type Routes,
} from '../test/fixtures'

vi.mock('chart.js', () => {
  class Chart {
    static register() {}
    destroy() {}
  }
  return { Chart, CategoryScale: {}, LinearScale: {}, LineController: {}, LineElement: {}, PointElement: {}, Legend: {}, Tooltip: {} }
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
      body: init?.body,
    }))
    .filter((c) => c.method === method)
}

async function openTab(name: string) {
  render(<App />)
  await userEvent.click(await screen.findByRole('tab', { name }))
}

const FINANCE: Routes = { '/api/auth/me': () => json(financeMe) }
const CRO: Routes = { '/api/auth/me': () => json(croMe) }
const CAN_TAKE: Routes = { '/api/v1/snapshots': () => json({ ...snapshotList, can_take: true }) }

function csv(name = 'october.csv') {
  return new File(['Month,Customer,Amount\n2026-10,Acme,100\n'], name, { type: 'text/csv' })
}

afterEach(() => vi.restoreAllMocks())

describe('navigation', () => {
  it('shows Snapshots to everyone but Actuals and Accuracy only to Finance, leadership and Administrators', async () => {
    mockFetch()
    const first = render(<App />)
    expect(await screen.findByRole('tab', { name: 'Snapshots' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Actuals' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Accuracy' })).not.toBeInTheDocument()
    first.unmount()

    for (const routes of [FINANCE, CRO]) {
      vi.restoreAllMocks()
      mockFetch(routes)
      const view = render(<App />)
      expect(await screen.findByRole('tab', { name: 'Actuals' })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: 'Accuracy' })).toBeInTheDocument()
      view.unmount()
    }
  })

  it('does not load reporting data until its tab is opened', async () => {
    mockFetch(FINANCE)
    render(<App />)
    await screen.findByText('Cedarfall Energy')
    expect(calls().some((c) => /snapshots|actuals|accuracy/.test(c.url))).toBe(false)
  })
})

describe('Snapshots', () => {
  it('lists snapshots newest first with kind, who took them and the stored totals', async () => {
    mockFetch()
    await openTab('Snapshots')
    const table = await screen.findByRole('table', { name: 'Snapshots' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText(/#2 2026-11 manual/)).toBeInTheDocument()
    expect(within(rows[0]).getByText('Manual')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Board pack')).toBeInTheDocument()
    expect(within(rows[0]).getByText('$80,000')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Scheduled')).toBeInTheDocument()
    expect(within(rows[1]).getByText('scheduled job')).toBeInTheDocument()
    expect(within(rows[1]).getByText('$18,000')).toBeInTheDocument()
    expect(within(rows[1]).getByText('$60,000')).toBeInTheDocument()
  })

  it('states the schedule and the last job outcome', async () => {
    mockFetch()
    await openTab('Snapshots')
    const schedule = await screen.findByRole('region', { name: 'Snapshot schedule' })
    expect(within(schedule).getByText(/Tuesday, December 1, 2026/)).toBeInTheDocument()
    expect(within(schedule).getByText(/scheduled snapshot has been taken/)).toBeInTheDocument()
    expect(within(schedule).getByText(/Last job run .*: completed\. Took the November 2026 snapshot #1\./)).toBeInTheDocument()
    expect(within(schedule).getByText(/can never be edited or deleted/)).toBeInTheDocument()
  })

  it('shows a failed scheduled job so it cannot go unnoticed', async () => {
    const base = snapshotList.last_job
    if (!base) throw new Error('fixture')
    mockFetch({
      '/api/v1/snapshots': () =>
        json({ ...snapshotList, scheduled_taken_this_month: false, last_job: { ...base, status: 'failed', message: 'RuntimeError: boom' } }),
    })
    await openTab('Snapshots')
    const schedule = await screen.findByRole('region', { name: 'Snapshot schedule' })
    expect(within(schedule).getByText(/has not been taken yet/)).toBeInTheDocument()
    expect(within(schedule).getByText(/failed\. RuntimeError: boom/)).toHaveClass('text-danger')
  })

  it('offers no snapshot button to people who may not take one', async () => {
    mockFetch()
    await openTab('Snapshots')
    await screen.findByRole('table', { name: 'Snapshots' })
    expect(screen.queryByRole('button', { name: 'Take snapshot now' })).not.toBeInTheDocument()
    expect(screen.getByText(/Finance and Administrators can take a snapshot on demand/)).toBeInTheDocument()
  })

  it('takes a snapshot with a note, using the CSRF token, and opens it', async () => {
    mockFetch({
      ...FINANCE,
      ...CAN_TAKE,
      'POST /api/v1/snapshots': () => json({ ...snapshotList.items[0], id: 7 }, 201),
      '/api/v1/snapshots/7': () => json({ ...snapshotTotals, snapshot: { ...snapshotList.items[0], id: 7 } }),
    })
    await openTab('Snapshots')
    await userEvent.type(await screen.findByLabelText('Snapshot note'), 'Month end')
    await userEvent.click(screen.getByRole('button', { name: 'Take snapshot now' }))

    expect(await screen.findByText('Snapshot #7 taken. It can no longer be changed.')).toBeInTheDocument()
    const [post] = calls('POST').filter((c) => c.url.endsWith('/v1/snapshots'))
    expect(post.headers['X-CSRF-Token']).toBe('csrf-token-123')
    expect(JSON.parse(String(post.body))).toEqual({ note: 'Month end' })
    expect(await screen.findByRole('heading', { name: /Snapshot #7/ })).toBeInTheDocument()
  })

  it('shows why a snapshot could not be taken', async () => {
    mockFetch({
      ...CAN_TAKE,
      'POST /api/v1/snapshots': () => json({ detail: { code: 'forbidden_role', message: 'Taking a snapshot is limited to Administrator, Finance.' } }, 403),
    })
    await openTab('Snapshots')
    await userEvent.click(await screen.findByRole('button', { name: 'Take snapshot now' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Taking a snapshot is limited to Administrator, Finance.')
  })

  it('shows the stored monthly totals exactly as the server returned them', async () => {
    mockFetch({ '/api/v1/snapshots/1': () => json({ ...snapshotTotals, combined_total: '12345.00' }) })
    await openTab('Snapshots')
    await userEvent.click(await screen.findByRole('button', { name: /#1 2026-11 scheduled/ }))

    const detail = await screen.findByRole('region', { name: 'Snapshot detail' })
    const cards = within(await within(detail).findByLabelText('Snapshot totals'))
    expect(cards.getByText('$18,000')).toBeInTheDocument()
    expect(cards.getByText('$60,000')).toBeInTheDocument()
    expect(cards.getByText('$12,345')).toBeInTheDocument() // displayed, not recomputed
    const table = within(detail).getByRole('table', { name: 'Snapshot monthly totals' })
    expect(within(table).getByRole('columnheader', { name: 'Nov 2026' })).toBeInTheDocument()
    expect(within(within(table).getByRole('row', { name: /Weighted pipeline/ })).getByText('$10,000')).toBeInTheDocument()
    expect(within(detail).getByText(/1 deal\(s\) worth \$596,000 weighted/)).toBeInTheDocument()
    expect(within(detail).getByText(/checksum/)).toBeInTheDocument()
  })

  it('applies the PRD filters to a snapshot and says what that means', async () => {
    mockFetch({ '/api/v1/snapshots/1': () => json({ ...snapshotTotals, filters_applied: true, unphased: null, outside_window_weighted: null }) })
    await openTab('Snapshots')
    await userEvent.click(await screen.findByRole('button', { name: /#1 2026-11 scheduled/ }))
    const detail = await screen.findByRole('region', { name: 'Snapshot detail' })
    await userEvent.selectOptions(await within(detail).findByLabelText('Snapshot Owner'), 'Shah')

    await waitFor(() => expect(calls().some((c) => c.url.includes('/v1/snapshots/1?owner=Shah'))).toBe(true))
    expect(await within(detail).findByText(/Filters apply to what was stored at the time/)).toBeInTheDocument()
    expect(within(detail).getByRole('button', { name: 'Clear filters' })).toBeInTheDocument()
  })

  it('checks a snapshot’s integrity and reports a mismatch loudly', async () => {
    mockFetch()
    await openTab('Snapshots')
    await userEvent.click(await screen.findByRole('button', { name: /#1 2026-11 scheduled/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Verify integrity' }))
    expect(await screen.findByText('Intact: matches its checksum')).toBeInTheDocument()

    vi.restoreAllMocks()
    mockFetch({ '/api/v1/snapshots/1/verify': () => json({ snapshot_id: 1, intact: false, checksum: 'x' }) })
    await userEvent.click(screen.getByRole('button', { name: 'Verify integrity' }))
    expect(await screen.findByText('ALTERED: does not match its checksum')).toBeInTheDocument()
  })

  it('links exports for a snapshot that carry the chosen filters and CSV view', async () => {
    mockFetch()
    await openTab('Snapshots')
    await userEvent.click(await screen.findByRole('button', { name: /#1 2026-11 scheduled/ }))
    const detail = await screen.findByRole('region', { name: 'Snapshot detail' })
    await userEvent.selectOptions(await within(detail).findByLabelText('Snapshot Region'), 'UK')
    const exports = within(await within(detail).findByRole('group', { name: 'Export the snapshot' }))

    expect(exports.getByRole('link', { name: 'Excel (.xlsx)' })).toHaveAttribute('href', '/api/v1/snapshots/1/export?region=UK&format=xlsx')
    expect(exports.getByRole('link', { name: 'CSV' })).toHaveAttribute('href', '/api/v1/snapshots/1/export?region=UK&format=csv&view=monthly')
    await userEvent.selectOptions(exports.getByLabelText('CSV contents'), 'projects')
    expect(exports.getByRole('link', { name: 'CSV' })).toHaveAttribute('href', '/api/v1/snapshots/1/export?region=UK&format=csv&view=projects')
    expect(exports.getByText(/export time and the filters applied/)).toBeInTheDocument()
  })

  it('has an empty state and an error state that does not touch the forecast', async () => {
    mockFetch({ '/api/v1/snapshots': () => json({ ...snapshotList, items: [], last_job: null }) })
    await openTab('Snapshots')
    expect(await screen.findByText('No snapshots yet.')).toBeInTheDocument()

    vi.restoreAllMocks()
    mockFetch({ '/api/v1/snapshots': () => json({ detail: 'boom' }, 500) })
    await userEvent.click(screen.getByRole('tab', { name: 'Forecast' }))
    await userEvent.click(screen.getByRole('tab', { name: 'Snapshots' }))
    expect(await screen.findByText('Snapshots could not be loaded.')).toBeInTheDocument()
    expect(screen.getByText('The live forecast is unaffected.')).toBeInTheDocument()
  })
})

describe('exporting the current forecast', () => {
  it('puts export links on the Pipeline tab that follow the filters', async () => {
    mockFetch()
    await openTab('Pipeline')
    const group = within(await screen.findByRole('group', { name: 'Export the current forecast' }))
    expect(group.getByRole('link', { name: 'Excel (.xlsx)' })).toHaveAttribute('href', '/api/v1/forecast/export?format=xlsx')

    await userEvent.selectOptions(screen.getByLabelText('Region'), 'UK')
    await waitFor(() =>
      expect(group.getByRole('link', { name: 'Excel (.xlsx)' })).toHaveAttribute('href', '/api/v1/forecast/export?region=UK&format=xlsx'),
    )
    expect(group.getByRole('link', { name: 'CSV' })).toHaveAttribute('href', '/api/v1/forecast/export?region=UK&format=csv&view=monthly')
  })
})

describe('Actuals', () => {
  it('lets Finance check a file first, then shows the reconciliation without saving', async () => {
    mockFetch({
      ...FINANCE,
      'POST /api/v1/actuals/uploads': () => json({ dry_run: true, status: 'completed', upload_id: null, report: reconciliation }),
    })
    await openTab('Actuals')
    const picker = await screen.findByLabelText('Actuals file')
    expect(screen.getByRole('button', { name: 'Check file' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Upload actuals' })).toBeDisabled()

    await userEvent.upload(picker, csv())
    await userEvent.click(screen.getByRole('button', { name: 'Check file' }))

    const report = await screen.findByRole('region', { name: 'Reconciliation report' })
    expect(within(report).getByText('File checked: it is valid. Nothing has been saved yet.')).toBeInTheDocument()
    expect(within(report).getByText(/3 rows read, 0 with problems/)).toBeInTheDocument()
    expect(within(report).getByText(/Ignored columns: Notes/)).toBeInTheDocument()
    const month = within(within(report).getByRole('table', { name: 'Reconciliation by month' })).getByRole('row', { name: /Oct 2026/ })
    expect(within(month).getAllByText('$3,000.00')).toHaveLength(2) // actual in file, and the forecast now
    const [post] = calls('POST')
    expect(post.url).toBe('/api/v1/actuals/uploads?filename=october.csv&dry_run=true')
    expect(post.headers['X-CSRF-Token']).toBe('csrf-token-123')
    expect(post.body).toBeInstanceOf(File)
  })

  it('uploads the file and refreshes the history', async () => {
    let loaded = false
    mockFetch({
      ...FINANCE,
      'POST /api/v1/actuals/uploads': () => {
        loaded = true
        return json({ dry_run: false, status: 'completed', upload_id: 9, report: reconciliation })
      },
      '/api/v1/actuals/uploads': () => json(loaded ? [{ ...uploadsRow(), id: 9, filename: 'october.csv' }] : []),
    })
    await openTab('Actuals')
    expect(await screen.findByText('No actuals have been uploaded yet.')).toBeInTheDocument()
    await userEvent.upload(screen.getByLabelText('Actuals file'), csv())
    await userEvent.click(screen.getByRole('button', { name: 'Upload actuals' }))

    expect(await screen.findByText('Actuals loaded (upload #9).')).toBeInTheDocument()
    const history = await screen.findByRole('table', { name: 'Uploads' })
    expect(within(history).getByText('october.csv')).toBeInTheDocument()
    expect(calls('POST')[0].url).toBe('/api/v1/actuals/uploads?filename=october.csv')
  })

  it('lists every problem with its row when a file is rejected', async () => {
    mockFetch({
      ...FINANCE,
      'POST /api/v1/actuals/uploads': () =>
        json(
          {
            detail: {
              code: 'actuals_rejected',
              message: 'Nothing was loaded: 2 row(s) have problems. Fix them and upload again.',
              upload_id: 5,
              report: {
                ...reconciliation,
                rows_rejected: 2,
                rows_new: undefined,
                errors_total: 2,
                errors: [
                  { row: 3, code: 'unknown_customer', message: "No customer is named 'Nobody'.", field: 'customer' },
                  { row: 8, code: 'invalid_amount', message: "Amount 'abc' is not a number.", field: 'amount' },
                ],
              },
            },
          },
          422,
        ),
    })
    await openTab('Actuals')
    await userEvent.upload(await screen.findByLabelText('Actuals file'), csv('bad.csv'))
    await userEvent.click(screen.getByRole('button', { name: 'Upload actuals' }))

    const alert = await screen.findByText(/Nothing was loaded: 2 row\(s\) have problems/)
    expect(alert).toBeInTheDocument()
    const problems = within(await screen.findByRole('table', { name: 'Upload problems' }))
    const first = within(problems.getByRole('row', { name: /No customer is named/ }))
    expect(first.getByText('3')).toBeInTheDocument()
    expect(first.getByText('customer')).toBeInTheDocument()
    expect(within(problems.getByRole('row', { name: /not a number/ })).getByText('8')).toBeInTheDocument()
  })

  it('shows a clear message when the file itself cannot be used', async () => {
    mockFetch({
      ...FINANCE,
      'POST /api/v1/actuals/uploads': () =>
        json({ detail: { code: 'missing_columns', message: 'The file needs these columns: month. Found: Customer, Amount.' } }, 400),
    })
    await openTab('Actuals')
    await userEvent.upload(await screen.findByLabelText('Actuals file'), csv())
    await userEvent.click(screen.getByRole('button', { name: 'Check file' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The file needs these columns: month.')
  })

  it('is read-only for leadership: history and totals, no upload form', async () => {
    mockFetch(CRO)
    await openTab('Actuals')
    expect(await screen.findByRole('table', { name: 'Uploads' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Actuals file')).not.toBeInTheDocument()
    expect(screen.getByText(/Finance and Administrators load actuals/)).toBeInTheDocument()
    const months = within(screen.getByRole('table', { name: 'Actuals by month' }))
    expect(months.getByText('$3,000.00', { selector: 'td:nth-child(2)' })).toBeInTheDocument()
    expect(actualMonths).toHaveLength(1)
  })

  it('records a rejected attempt in the history', async () => {
    mockFetch(FINANCE)
    await openTab('Actuals')
    const history = within(await screen.findByRole('table', { name: 'Uploads' }))
    expect(history.getByText('Rejected (2 bad rows)')).toBeInTheDocument()
    expect(history.getByText('Loaded')).toBeInTheDocument()
  })
})

function uploadsRow() {
  return {
    id: 1,
    filename: 'x.csv',
    status: 'completed',
    uploaded_at: '2026-11-03T10:00:00Z',
    uploaded_by_label: 'Fin Ance',
    rows_total: 3,
    rows_accepted: 3,
    rows_rejected: 0,
    rows_unchanged: 0,
    rows_replaced: 0,
    total_amount: '3000.00',
    months: ['2026-10-01'],
  }
}

describe('Accuracy', () => {
  it('shows each look-ahead against the actual with variance and flags months outside the target', async () => {
    mockFetch(FINANCE)
    await openTab('Accuracy')
    const table = await screen.findByRole('table', { name: 'Accuracy by month' })
    const row = within(within(table).getByRole('row', { name: /Oct 2026/ }))

    expect(row.getByText('$3,000', { selector: 'td:nth-child(2)' })).toBeInTheDocument() // actual
    expect(row.getByText('$3,200')).toBeInTheDocument() // M-1 forecast
    expect(row.getByText(/\+6\.67%/)).toBeInTheDocument()
    expect(row.getByText('Outside ±5%')).toBeInTheDocument() // flagged
    expect(row.getByText('No snapshot')).toBeInTheDocument() // M-2 missing, not estimated
    expect(row.getByText(/0\.00%/)).toBeInTheDocument() // M-3 within target
    expect(within(table).getAllByText('Outside ±5%')).toHaveLength(2) // the month and the M-1 total
  })

  it('summarises the M-3 target and the totals', async () => {
    mockFetch(FINANCE)
    await openTab('Accuracy')
    expect(await screen.findByText(/M-3: 1 of 1 month\(s\) within target/)).toBeInTheDocument()
    expect(screen.getByText(/in total 0\.00% \(within the target\)/)).toBeInTheDocument()
    const footer = within(screen.getByRole('table', { name: 'Accuracy by month' }).querySelector('tfoot') as HTMLElement)
    expect(footer.getByText(/\$3,200 vs \$3,000: \+6\.67%/)).toBeInTheDocument()
  })

  it('explains an empty report', async () => {
    mockFetch({
      ...FINANCE,
      '/api/v1/accuracy': () =>
        json({ ...accuracy, months: [], totals: accuracy.totals.map((t) => ({ ...t, months_compared: 0, months_flagged: 0 })) }),
    })
    await openTab('Accuracy')
    expect(await screen.findByText('No actuals have been loaded.')).toBeInTheDocument()
    expect(screen.getByText(/No month has both actuals and an M-3 snapshot yet/)).toBeInTheDocument()
  })

  it('shows an error state with a retry when the report cannot be loaded', async () => {
    mockFetch({ ...FINANCE, '/api/v1/accuracy': () => json({ detail: 'boom' }, 500) })
    await openTab('Accuracy')
    expect(await screen.findByText('The accuracy report could not be loaded.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('change history', () => {
  it('flags edits made to a month that was already snapshotted', async () => {
    mockFetch({
      '/api/v1/projects/1/history': () =>
        json({ items: [{ ...history[0], post_snapshot: true }, history[1]], total_items: 2 }),
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Data Platform Build' }))
    const panel = await screen.findByRole('complementary', { name: /Details for Data Platform Build/ })
    const section = await within(panel).findByRole('region', { name: 'Change history' })
    const entries = await within(section).findAllByRole('listitem')
    expect(within(entries[0]).getByText('Post-snapshot change')).toBeInTheDocument()
    expect(within(entries[1]).queryByText('Post-snapshot change')).not.toBeInTheDocument()
  })
})
