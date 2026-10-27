import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { formatMoney } from '../format'
import { months, projects, totals } from '../test/fixtures'
import ForecastGrid from './ForecastGrid'

function renderGrid(overrides: Partial<Parameters<typeof ForecastGrid>[0]> = {}) {
  return render(
    <ForecastGrid
      projects={projects}
      months={months}
      totals={totals}
      quickFilter=""
      onOpenProject={vi.fn()}
      renderAll
      {...overrides}
    />,
  )
}

describe('formatMoney', () => {
  it('formats whole-dollar currency with grouping and handles blanks and negatives', () => {
    expect(formatMoney('80950.00')).toBe('$80,950')
    expect(formatMoney(1234567)).toBe('$1,234,567')
    expect(formatMoney(-50)).toBe('-$50')
    expect(formatMoney(null)).toBe('')
    expect(formatMoney('')).toBe('')
  })
})

describe('ForecastGrid', () => {
  it('renders one row per project with a column per fiscal month, grouped by fiscal year', async () => {
    renderGrid()

    expect(await screen.findByText('Cedarfall Energy')).toBeInTheDocument()
    expect(screen.getByText('Kestrel Life')).toBeInTheDocument()
    expect(document.querySelectorAll('.ag-center-cols-container [row-id]')).toHaveLength(2)
    for (const label of ['Sep 2026', 'Oct 2026', 'FY27', 'Total']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('formats amounts as currency and leaves blank months empty', async () => {
    renderGrid()
    await screen.findByText('Cedarfall Energy')

    expect(document.querySelector('[row-id="1"] [col-id="2026-10-01"]')).toHaveTextContent('$1,251')
    expect(document.querySelector('[row-id="1"] [col-id="2026-09-01"]')).toHaveTextContent(/^$/)
    expect(document.querySelector('[row-id="2"] [col-id="2026-09-01"]')).toHaveTextContent(
      '$80,000',
    )
  })

  it('freezes the customer and project columns', async () => {
    renderGrid()
    await screen.findByText('Cedarfall Energy')

    const pinned = document.querySelector('.ag-pinned-left-cols-container')
    expect(pinned).toHaveTextContent('Cedarfall Energy')
    expect(pinned).toHaveTextContent('Data Platform Build')
    expect(pinned?.querySelector('[col-id="2026-10-01"]')).toBeNull()
  })

  it('shows status and an attention indicator only where the data says so', async () => {
    renderGrid()
    await screen.findByText('Cedarfall Energy')

    expect(screen.getAllByText('Needs attention')).toHaveLength(1)
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('Completed')).toBeInTheDocument()
  })

  it('shows the backend-calculated totals row rather than summing in the browser', async () => {
    // The total passed in deliberately differs from the sum of the rows.
    renderGrid({ totals: { ...totals, grand_total: '999.00' } })
    await screen.findByText('Cedarfall Energy')

    const pinnedBottom = document.querySelector('.ag-floating-bottom')
    expect(pinnedBottom).toHaveTextContent('Total (2 projects)')
    expect(pinnedBottom).toHaveTextContent('$999')
    expect(pinnedBottom).toHaveTextContent('$80,000')
  })

  it('opens a project from its name and offers no cell editing', async () => {
    const onOpenProject = vi.fn()
    renderGrid({ onOpenProject })
    await screen.findByText('Cedarfall Energy')

    screen.getByRole('button', { name: 'Policy Admin' }).click()
    expect(onOpenProject).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }))
    expect(document.querySelector('.ag-cell-inline-editing')).toBeNull()
    expect(document.querySelector('input.ag-input-field-input')).toBeNull()
  })
})
