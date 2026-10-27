// The source workbook carries no currency, so amounts are shown with a plain "$".
const currency = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

export function formatMoney(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  const number = Number(value)
  if (Number.isNaN(number)) return ''
  return `${number < 0 ? '-' : ''}$${currency.format(Math.abs(number))}`
}

export function formatMonthLabel(iso: string): string {
  const [year, month] = iso.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString('en-US', {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

const exact = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Dollars and cents, for places where the exact stored value matters (audit history). */
export function formatMoneyExact(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return `$${exact.format(Number(value))}`
}

/**
 * Turn what someone typed into the plain number the API accepts, or null if it is not a plain
 * non-negative amount. Formulas and text are refused here for a quick message; the server
 * enforces the same rule independently.
 */
export function parseAmountInput(raw: string): string | null {
  const text = raw.trim()
  if (!/^\$?\s*\d[\d,]*(\.\d{1,2})?$/.test(text)) return null
  const plain = text.replace(/[$,\s]/g, '')
  return /^\d{1,12}(\.\d{1,2})?$/.test(plain) ? plain : null
}
