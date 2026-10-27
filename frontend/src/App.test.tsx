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

  it('keeps the Milestone 0 shell and API health indicator', async () => {
    mockFetch()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'The Chosen One' })).toBeInTheDocument()
    expect(await screen.findByText('API connected (ok)')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/health', expect.any(Object))
  })

  it('shows the signed-out screen and never requests project data', async () => {
    mockFetch({ '/api/auth/me': () => json(signedOut) })
    render(<App />)

    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toHaveAttribute(
      'href',
      '/api/auth/login',
    )
    expect(screen.queryByText('Cedarfall Energy')).not.toBeInTheDocument()
    expect(calledPaths().some((p) => p.includes('/api/v1/'))).toBe(false)
  })

  it('shows a loading state while checking the session', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => undefined))
    render(<App />)
    expect(screen.getByText('Checking your session…')).toBeInTheDocument()
  })

  it.each([
    ['not_provisioned', /has not been given access/],
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

  it('shows who is signed in with their roles, and loads the forecast', async () => {
    mockFetch()
    render(<App />)

    expect(await screen.findByText('Avery Chen')).toBeInTheDocument()
    expect(screen.getByText('Client Partner')).toBeInTheDocument()
    expect(await screen.findByText('Cedarfall Energy')).toBeInTheDocument()
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
    expect(await screen.findByText('We could not check whether you are signed in.')).toBeInTheDocument()

    down = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument()
  })
})

describe('App forecast states', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows a loading state, then the projects in the grid', async () => {
    mockFetch()
    render(<App />)

    expect(await screen.findByText('Loading the forecast…')).toBeInTheDocument()
    expect(await screen.findByText('Cedarfall Energy')).toBeInTheDocument()
    expect(screen.getByText('Kestrel Life')).toBeInTheDocument()
    expect(screen.getByText(/2 projects · you can edit 1/)).toBeInTheDocument()
  })

  it('says read-only when the user can edit nothing', async () => {
    mockFetch({
      '/api/v1/projects': () =>
        json({
          items: [{ ...projects[0], can_edit: false }],
          page: 1,
          page_size: 500,
          total_items: 1,
          total_pages: 1,
        }),
    })
    render(<App />)
    expect(await screen.findByText('1 projects · read-only')).toBeInTheDocument()
  })

  it('shows an empty state with the import command when nothing is imported', async () => {
    mockFetch({
      '/api/v1/projects': () => json(emptyPage),
      '/api/v1/imports/latest': () => json({ detail: 'Import run not found' }, 404),
    })
    render(<App />)

    expect(await screen.findByText('No projects have been imported yet.')).toBeInTheDocument()
    expect(screen.getByText('uv run python -m app.migration')).toBeInTheDocument()
  })

  it('shows an error state with retry when the forecast cannot be loaded', async () => {
    let failing = true
    mockFetch({
      '/api/v1/projects': () => (failing ? json({ detail: 'boom' }, 500) : json(emptyPage)),
    })
    render(<App />)
    expect(await screen.findByText('The forecast could not be loaded.')).toBeInTheDocument()

    failing = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('No projects have been imported yet.')).toBeInTheDocument()
  })

  it('filters the grid with the search box', async () => {
    mockFetch()
    render(<App />)
    await screen.findByText('Cedarfall Energy')
    await userEvent.type(screen.getByLabelText('Search customer or project'), 'Kestrel')

    await waitFor(() => expect(screen.queryByText('Cedarfall Energy')).not.toBeInTheDocument())
    expect(screen.getByText('Kestrel Life')).toBeInTheDocument()
  })

  it('opens project details from the grid', async () => {
    mockFetch()
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Data Platform Build' }))

    const panel = await screen.findByRole('complementary', { name: /Details for Data Platform Build/ })
    expect(await within(panel).findByText('No Client Partner')).toBeInTheDocument()
    expect(within(panel).getByText('Riley Shah')).toBeInTheDocument()
  })
})
