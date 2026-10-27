import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { json, mockApi, okRoutes } from './test/fixtures'

const emptyPage = { items: [], page: 1, page_size: 500, total_items: 0, total_pages: 0 }

describe('App', () => {
  afterEach(() => vi.restoreAllMocks())

  it('keeps the Milestone 0 shell and API health indicator', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi(okRoutes))

    render(<App />)

    expect(screen.getByRole('heading', { name: 'The Chosen One' })).toBeInTheDocument()
    expect(await screen.findByText('API connected (ok)')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/health', expect.any(Object))
  })

  it('shows a loading state, then the projects in the grid', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi(okRoutes))

    render(<App />)
    expect(screen.getByText('Loading the forecast…')).toBeInTheDocument()

    expect(await screen.findByText('Cedarfall Energy')).toBeInTheDocument()
    expect(screen.getByText('Kestrel Life')).toBeInTheDocument()
    expect(screen.getByText('2 projects · read-only')).toBeInTheDocument()
  })

  it('shows an empty state with the import command when nothing is imported', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      mockApi({
        ...okRoutes,
        '/api/v1/projects': () => json(emptyPage),
        '/api/v1/imports/latest': () => json({ detail: 'Import run not found' }, 404),
      }),
    )

    render(<App />)

    expect(await screen.findByText('No projects have been imported yet.')).toBeInTheDocument()
    expect(screen.getByText('uv run python -m app.migration')).toBeInTheDocument()
  })

  it('shows an error state with retry when the forecast cannot be loaded', async () => {
    let failing = true
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      mockApi({
        ...okRoutes,
        '/api/v1/projects': () => (failing ? json({ detail: 'boom' }, 500) : json(emptyPage)),
      }),
    )

    render(<App />)
    expect(await screen.findByText('The forecast could not be loaded.')).toBeInTheDocument()

    failing = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('No projects have been imported yet.')).toBeInTheDocument()
  })

  it('opens project details from the grid, read-only', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi(okRoutes))

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Data Platform Build' }))

    const panel = await screen.findByRole('complementary', {
      name: /Details for Data Platform Build/,
    })
    expect(await within(panel).findByText('No Client Partner')).toBeInTheDocument()
    expect(within(panel).getByText('Riley Shah')).toBeInTheDocument()
    expect(within(panel).getByText(/Read-only/)).toBeInTheDocument()
    expect(within(panel).queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('filters the grid with the search box', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(mockApi(okRoutes))

    render(<App />)
    await screen.findByText('Cedarfall Energy')
    await userEvent.type(screen.getByLabelText('Search customer or project'), 'Kestrel')

    await waitFor(() => expect(screen.queryByText('Cedarfall Energy')).not.toBeInTheDocument())
    expect(screen.getByText('Kestrel Life')).toBeInTheDocument()
  })
})
