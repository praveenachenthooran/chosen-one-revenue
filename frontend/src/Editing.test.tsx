import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { json, mockApi, okRoutes, projects, totals, type Routes } from './test/fixtures'

const EDIT_URL = 'PUT /api/v1/projects/1/revenue/2026-10-01'

function mockFetch(overrides: Routes = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi({ ...okRoutes, ...overrides }))
}

function putCalls() {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([, init]) => init?.method === 'PUT')
    .map(([url, init]) => ({
      url: String(url),
      body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      headers: init?.headers as Record<string, string>,
    }))
}

const savedResult = {
  project_id: 1,
  month: '2026-10-01',
  amount: '1500.00',
  previous_amount: '1250.50',
  changed: true,
  project_total: '1500.00',
  revision_id: 99,
}

/** The month cell for a project in the (centre) scrolling part of the grid. */
function cell(projectId: number, month: string): HTMLElement {
  const found = document.querySelector(
    `.ag-center-cols-container [row-id="${projectId}"] [col-id="${month}"]`,
  )
  if (!found) throw new Error(`no cell for project ${projectId} ${month}`)
  return found as HTMLElement
}

async function typeInto(target: HTMLElement, text: string) {
  await userEvent.dblClick(target)
  const input = await waitFor(() => {
    const el = target.querySelector('input')
    if (!el) throw new Error('editor not open')
    return el
  })
  await userEvent.clear(input)
  await userEvent.type(input, `${text}{Enter}`)
}

describe('editing in the grid', () => {
  afterEach(() => vi.restoreAllMocks())

  it('lets the owner edit a month, saving with the expected previous value and CSRF token', async () => {
    mockFetch({ [EDIT_URL]: () => json(savedResult) })
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    expect(cell(1, '2026-10-01')).toHaveClass('cell-editable')
    await typeInto(cell(1, '2026-10-01'), '1500')

    await waitFor(() => expect(putCalls()).toHaveLength(1))
    const [call] = putCalls()
    expect(call.url).toBe('/api/v1/projects/1/revenue/2026-10-01')
    expect(call.body).toEqual({ amount: '1500', expected_amount: '1250.50', reason: null })
    expect(call.headers['X-CSRF-Token']).toBe('csrf-token-123')

    expect(await screen.findByText('Saved Oct 2026 for Data Platform Build.')).toBeInTheDocument()
    await waitFor(() => expect(cell(1, '2026-10-01')).toHaveTextContent('$1,500'))
  })

  it('shows a saving indicator while the request is in flight', async () => {
    let release: (response: Response) => void = () => undefined
    mockFetch({ [EDIT_URL]: () => new Promise<Response>((resolve) => (release = resolve)) as never })
    // The handler contract returns a Response; give the mock a pending promise instead.
    vi.mocked(fetch).mockImplementation((input, init) => {
      if (init?.method === 'PUT') return new Promise<Response>((resolve) => (release = resolve))
      return mockApi(okRoutes)(input, init)
    })
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    await typeInto(cell(1, '2026-10-01'), '1600')

    expect(await screen.findByText('Saving…')).toBeInTheDocument()
    await waitFor(() => expect(cell(1, '2026-10-01')).toHaveClass('cell-saving'))
    release(json({ ...savedResult, amount: '1600.00', project_total: '1600.00' }))
    await waitFor(() => expect(screen.queryByText('Saving…')).not.toBeInTheDocument())
    expect(await screen.findByText(/Saved Oct 2026/)).toBeInTheDocument()
  })

  it.each(['=90000-55000', 'abc', '-5', '1e5', '12.345', ''])(
    'rejects %j with a message and never calls the server',
    async (bad) => {
      mockFetch({ [EDIT_URL]: () => json(savedResult) })
      render(<App />)
      await screen.findByText('Cedarfall Energy')

      await typeInto(cell(1, '2026-10-01'), bad)

      expect(await screen.findByRole('alert')).toHaveTextContent(/enter a plain number/i)
      expect(putCalls()).toHaveLength(0)
      expect(cell(1, '2026-10-01')).toHaveTextContent('$1,251') // unchanged
    },
  )

  it('accepts a typed dollar sign and thousands separators', async () => {
    mockFetch({ [EDIT_URL]: () => json(savedResult) })
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    await typeInto(cell(1, '2026-10-01'), '$12,500.50')

    await waitFor(() => expect(putCalls()).toHaveLength(1))
    expect(putCalls()[0].body.amount).toBe('12500.50')
  })

  it('does not call the server when the value is unchanged', async () => {
    mockFetch()
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    await typeInto(cell(1, '2026-10-01'), '1250.5')

    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(putCalls()).toHaveLength(0)
  })

  it('keeps projects the user does not own read-only', async () => {
    mockFetch()
    render(<App />)
    await screen.findByText('Kestrel Life')

    const readOnly = cell(2, '2026-09-01')
    expect(readOnly).not.toHaveClass('cell-editable')
    await userEvent.dblClick(readOnly)
    expect(readOnly.querySelector('input')).toBeNull()
    expect(putCalls()).toHaveLength(0)
  })

  it('shows the server refusal, naming the owner, and leaves the value alone', async () => {
    const message = 'You cannot edit Cedarfall Energy - Data Platform Build: it is owned by Riley Shah.'
    mockFetch({
      [EDIT_URL]: () => json({ detail: { code: 'not_project_owner', message, owners: [] } }, 403),
    })
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    await typeInto(cell(1, '2026-10-01'), '2000')

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(cell(1, '2026-10-01')).toHaveTextContent('$1,251')
  })

  it('reports a server validation error', async () => {
    mockFetch({
      [EDIT_URL]: () =>
        json({ detail: { code: 'invalid_amount', message: 'Enter a plain number.' } }, 422),
    })
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    await typeInto(cell(1, '2026-10-01'), '2000')
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter a plain number.')
  })

  it('on a conflict explains it and loads the latest value', async () => {
    let reads = 0
    mockFetch({
      [EDIT_URL]: () =>
        json(
          { detail: { code: 'conflict', message: 'Someone else changed this value.', current_amount: '1800.00' } },
          409,
        ),
      '/api/v1/projects': () => {
        reads += 1
        const latest = reads > 1 ? { ...projects[0], monthly: { '2026-10-01': '1800.00' }, total_revenue: '1800.00' } : projects[0]
        return json({ items: [latest, projects[1]], page: 1, page_size: 500, total_items: 2, total_pages: 1 })
      },
    })
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    await typeInto(cell(1, '2026-10-01'), '2000')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Oct 2026: Someone else changed this value. The latest value has been loaded.',
    )
    await waitFor(() => expect(cell(1, '2026-10-01')).toHaveTextContent('$1,800'))
  })

  it('returns to sign-in if the session ended before the save', async () => {
    mockFetch({
      [EDIT_URL]: () => json({ detail: { code: 'not_authenticated', message: 'Sign in.' } }, 401),
    })
    render(<App />)
    await screen.findByText('Cedarfall Energy')

    await typeInto(cell(1, '2026-10-01'), '2000')

    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument()
  })

  it('refreshes the totals row from the server after a save', async () => {
    let totalsReads = 0
    mockFetch({
      [EDIT_URL]: () => json(savedResult),
      '/api/v1/revenue/monthly-totals': () => {
        totalsReads += 1
        return json(totalsReads > 1 ? { ...totals, grand_total: '99999.00' } : totals)
      },
    })
    render(<App />)
    await screen.findByText('Cedarfall Energy')
    await typeInto(cell(1, '2026-10-01'), '1500')

    await waitFor(() => expect(document.querySelector('.ag-floating-bottom')).toHaveTextContent('$99,999'))
  })
})

describe('editing in the project panel, and change history', () => {
  afterEach(() => vi.restoreAllMocks())

  async function openPanel(name: string) {
    await userEvent.click(await screen.findByRole('button', { name }))
    return screen.findByRole('complementary', { name: new RegExp(`Details for ${name}`) })
  }

  it('lists every month, imported values, and an input for each when the user can edit', async () => {
    mockFetch()
    render(<App />)
    const panel = await openPanel('Data Platform Build')

    expect(await within(panel).findByText('You can edit this project’s monthly forecast.')).toBeInTheDocument()
    const input = within(panel).getByLabelText('Revenue for Oct 2026')
    expect(input).toHaveValue('1250.50')
    expect(within(panel).getByLabelText('Revenue for Sep 2026')).toHaveValue('') // blank month, fillable
    expect(within(panel).getByText(/\$1,000 · edited/)).toBeInTheDocument() // the imported baseline
    expect(within(panel).getByText('Current total')).toBeInTheDocument()
  })

  it('saves a month from the panel with the optional reason', async () => {
    mockFetch({ [EDIT_URL]: () => json(savedResult) })
    render(<App />)
    const panel = await openPanel('Data Platform Build')

    await userEvent.type(await within(panel).findByLabelText(/Reason for changes/), 'Client added scope')
    const input = within(panel).getByLabelText('Revenue for Oct 2026')
    await userEvent.clear(input)
    await userEvent.type(input, '1500{Enter}')

    await waitFor(() => expect(putCalls()).toHaveLength(1))
    expect(putCalls()[0].body).toEqual({
      amount: '1500',
      expected_amount: '1250.50',
      reason: 'Client added scope',
    })
    expect(await screen.findByText('Saved Oct 2026 for Data Platform Build.')).toBeInTheDocument()
  })

  it('shows an inline error and keeps the draft when the panel edit is invalid', async () => {
    mockFetch({ [EDIT_URL]: () => json(savedResult) })
    render(<App />)
    const panel = await openPanel('Data Platform Build')

    const input = await within(panel).findByLabelText('Revenue for Oct 2026')
    await userEvent.clear(input)
    await userEvent.type(input, '=90000-55000{Enter}')

    expect((await within(panel).findAllByRole('alert'))[0]).toHaveTextContent(/enter a plain number/i)
    expect(input).toHaveValue('=90000-55000')
    expect(putCalls()).toHaveLength(0)
  })

  it('shows read-only months and no inputs for a project the user does not own', async () => {
    mockFetch()
    render(<App />)
    const panel = await openPanel('Policy Admin')

    expect(await within(panel).findByText(/Read-only: you are not an owner/)).toBeInTheDocument()
    expect(within(panel).queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('shows who changed what, when, from and to, and why', async () => {
    mockFetch()
    render(<App />)
    const panel = await openPanel('Data Platform Build')

    const section = await within(panel).findByRole('region', { name: 'Change history' })
    const entries = await within(section).findAllByRole('listitem')
    expect(entries).toHaveLength(2)
    expect(within(entries[0]).getByText('Oct 2026')).toBeInTheDocument()
    expect(within(entries[0]).getByText('$1,000.00 → $1,250.50')).toBeInTheDocument()
    expect(within(entries[0]).getByText(/Avery Chen · “Client added scope”/)).toBeInTheDocument()
    expect(entries[0].querySelector('time')).toHaveAttribute('datetime', '2026-10-20T15:30:00Z')
    expect(within(entries[1]).getByText('Imported from workbook')).toBeInTheDocument()
    expect(within(entries[1]).getByText(/Excel import/)).toBeInTheDocument()
  })

  it('refreshes the history after a save', async () => {
    let historyReads = 0
    mockFetch({
      [EDIT_URL]: () => json(savedResult),
      '/api/v1/projects/1/history': () => {
        historyReads += 1
        const extra = {
          id: 50, created_at: '2026-10-27T10:00:00Z', field: 'revenue', month: '2026-10-01',
          change_type: 'update', old_value: '1250.50', new_value: '1500.00', reason: null,
          actor_name: 'Avery Chen', from_import: false,
        }
        return json({ items: historyReads > 1 ? [extra] : [], total_items: 0 })
      },
    })
    render(<App />)
    const panel = await openPanel('Data Platform Build')
    expect(await within(panel).findByText('No changes recorded yet.')).toBeInTheDocument()

    const input = within(panel).getByLabelText('Revenue for Oct 2026')
    await userEvent.clear(input)
    await userEvent.type(input, '1500{Enter}')

    expect(await within(panel).findByText('$1,250.50 → $1,500.00')).toBeInTheDocument()
  })

  it('explains why history is hidden from people who may not see it', async () => {
    mockFetch()
    render(<App />)
    const panel = await openPanel('Policy Admin')

    expect(await within(panel).findByText(/Change history is visible to this project’s owners|Change history is visible to this project's owners/)).toBeInTheDocument()
  })
})
