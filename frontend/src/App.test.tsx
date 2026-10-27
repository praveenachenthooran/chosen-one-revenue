import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

describe('App', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the application shell and a successful API connection', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok', service: 'chosen-one-api' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<App />)

    expect(screen.getByRole('heading', { name: 'The Chosen One' })).toBeInTheDocument()
    expect(await screen.findByText('API connected (ok)')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/health', expect.any(Object))
  })
})

