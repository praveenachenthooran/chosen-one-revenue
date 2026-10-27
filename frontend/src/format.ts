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
