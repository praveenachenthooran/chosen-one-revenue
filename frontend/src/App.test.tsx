import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { json, mockApi, okRoutes, projects, signedOut } from './test/fixtures'

const emptyPage = { items: [], page: 1, page_size: 500, total_items: 0, total_pages: 0 }

function mockFetch(overrides: Parameters<typeof mockApi>[0] = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi({ ...okRoutes, ...overrides }))
}

function calledPaths() {
  return vi.mocked(fetch).mock.calls.map(([input]) => String(input))
}

describe('App shell and sign-in', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.history.replaceState(null, '', '/')
  })

  it('shows the Biggie Revenue sign-in and no developer status', async () => {
    mockFetch({ '/api/auth/me': () => json(signedOut) })
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Know where every project’s revenue is heading.' })).toBeInTheDocument()
    expect(screen.getByText('Biggie Revenue')).toBeInTheDocument()
    expect(screen.queryByText(/The Chosen One/)).not.toBeInTheDocument()
    expect(screen.queryByText(/API connected/)).not.toBeInTheDocument()
  })

  it('shows the signed-out screen and never requests project data', async () => {
    mockFetch({ '/api/auth/me': () => json(signedOut) })
    render(<App />)

    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toHaveAttribute('href', '/api/auth/login')
    expect(screen.queryByText('Cedarfall Energy')).not.toBeInTheDocument()
    expect(calledPaths().some((p) => p.includes('/api/v1/'))).toBe(false)
  })

  it('shows a loading state while checking the session', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => undefined))
    render(<App />)
    expect(screen.getByText('Checking your session…')).toBeInTheDocument()
  })

  it.each([
    ['not_provisioned', /has not been given access to Biggie Revenue/],
    ['email_unverified', /not verified/],
    ['not_configured', /has not been configured/],
    ['failed', /could not be completed/],
    ['something_new', /could not be completed/],
  ])('explains a %s sign-in failure and clears it from the URL', async (code, message) => {
    window.history.replaceState(null, '', `/?auth_error=${code}`)
    mockFetch({ '/api/auth/me': () => json(signedOut) })
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(window.location.search).toBe('')
  })

  it('shows who is signed in with their role, and lands a Client Partner on My projects', async () => {
    mockFetch()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'My projects' })).toBeInTheDocument()
    const sidebar = screen.getByRole('complementary')
    expect(within(sidebar).getByText('Avery Chen')).toBeInTheDocument()
    expect(within(sidebar).getByText('Client Partner')).toBeInTheDocument()
    expect(await screen.findByRole('row', { name: 'Open Cedarfall Energy' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Sign in with Google' })).not.toBeInTheDocument()
  })

  it('signs out with the CSRF token and returns to the sign-in screen', async () => {
    let logoutHeaders: Record<string, string> = {}
    mockFetch({
      'POST /api/auth/logout': ({ headers }) => {
        logoutHeaders = headers
        return json({ authenticated: false })
      },
    })
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument()
    expect(logoutHeaders['X-CSRF-Token']).toBe('csrf-token-123')
    expect(screen.queryByText('Cedarfall Energy')).not.toBeInTheDocument()
  })

  it('returns to sign-in with a message when the session ends mid-use', async () => {
    mockFetch({ '/api/v1/projects': () => json({ detail: { code: 'not_authenticated', message: 'Sign in.' } }, 401) })
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Your session has ended')
    expect(screen.getByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument()
  })

  it('offers a retry when the authentication check itself fails', async () => {
    let down = true
    mockFetch({ '/api/auth/me': () => (down ? json({ detail: 'boom' }, 500) : json(signedOut)) })
    render(<App />)
    expect(await screen.findByText(/We could not check whether you are signed in/)).toBeInTheDocument()

    down = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument()
  })
})

describe('My projects (Client Partner)', () => {
  afterEach(() => vi.restoreAllMocks())

  it('lists only the projects the partner owns, flagged and ordered, with no graph', async () => {
    mockFetch()
    render(<App />)

    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(1) // Kestrel Life belongs to someone else
    const row = rows[0]
    expect(within(row).getByText('Cedarfall Energy')).toBeInTheDocument()
    expect(within(row).getByText('Off Track')).toBeInTheDocument()
    expect(within(row).getByText('$21,000')).toBeInTheDocument()
    expect(within(row).getByText('Verify the change in monthly revenue')).toBeInTheDocument()
    expect(within(row).getByText('Medium')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument() // the graph lives in the project, not here
    expect(screen.queryByText('Kestrel Life')).not.toBeInTheDocument()
  })

  it('does not show technology, industry, region or mismatch columns', async () => {
    mockFetch()
    render(<App />)
    const table = await screen.findByRole('table')
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Project', 'Status', 'Owner', 'Revenue at stake', 'Required action', 'Priority'])
  })

  it('filters by status and says so when nothing matches', async () => {
    mockFetch()
    render(<App />)
    await screen.findByRole('row', { name: 'Open Cedarfall Energy' })

    await userEvent.selectOptions(screen.getByLabelText('Status'), 'on_track')

    expect(await screen.findByText('No projects match these filters')).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0])
    expect(await screen.findByRole('row', { name: 'Open Cedarfall Energy' })).toBeInTheDocument()
  })

  it('searches by customer or project', async () => {
    mockFetch()
    render(<App />)
    await screen.findByRole('row', { name: 'Open Cedarfall Energy' })
    await userEvent.type(screen.getByLabelText('Search'), 'zzz')
    expect(await screen.findByText('No projects match these filters')).toBeInTheDocument()
  })

  it('explains an empty list instead of showing a blank table', async () => {
    mockFetch({
      '/api/v1/projects': () => json({ items: [{ ...projects[0], can_edit: false }], page: 1, page_size: 500, total_items: 1, total_pages: 1 }),
    })
    render(<App />)
    expect(await screen.findByText('No projects are assigned to you yet')).toBeInTheDocument()
  })

  it('shows an error state with retry when the projects cannot be loaded', async () => {
    let failing = true
    mockFetch({ '/api/v1/projects': () => (failing ? json({ detail: 'boom' }, 500) : json(emptyPage)) })
    render(<App />)
    expect(await screen.findByText('Your projects could not be loaded.')).toBeInTheDocument()

    failing = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('No projects are assigned to you yet')).toBeInTheDocument()
  })

  it('opens the project from its row, with a way back', async () => {
    mockFetch()
    render(<App />)
    await userEvent.click(await screen.findByRole('row', { name: 'Open Cedarfall Energy' }))

    expect(await screen.findByRole('heading', { name: /Cedarfall Energy/ })).toBeInTheDocument()
    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Back to my projects/ }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'My projects' })).toBeInTheDocument())
  })
})
