import { formatMoney, formatMonthLabel } from '../format'

/**
 * Evidence from the checks, turned into plain business records. The engine stores evidence as a loose
 * record (amounts, month series, related rows); the main UI never shows it as JSON.
 */
export type EvidenceRow = { label: string; value: string }

const HIDDEN_KEYS = new Set(['id', 'project_id', 'finding_id', 'opportunity_id', 'user_id', 'run_id', 'investigation_id'])
const MONEY_KEY = /(amount|revenue|impact|total|before|after|forecast|committed|weighted|gap|difference|delta|prior|previous|current)/i
const PERCENT_KEY = /(percent|pct|share)/i
const ISO_MONTH = /^\d{4}-\d{2}(-\d{2})?(T.*)?$/

export function humanize(key: string): string {
  const text = key.replace(/_/g, ' ').trim()
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function isNumeric(value: unknown): value is number | string {
  if (typeof value === 'number') return Number.isFinite(value)
  return typeof value === 'string' && value.trim() !== '' && /^-?\d+(\.\d+)?$/.test(value.trim())
}

function scalar(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (isNumeric(value)) {
    if (PERCENT_KEY.test(key)) return `${Number(value).toFixed(1)}%`
    if (MONEY_KEY.test(key)) return formatMoney(value)
    return String(value)
  }
  if (typeof value === 'string' && ISO_MONTH.test(value)) {
    try {
      return formatMonthLabel(value.slice(0, 7) + '-01')
    } catch {
      return value
    }
  }
  return String(value)
}

function compound(key: string, value: unknown): string {
  if (Array.isArray(value)) {
    if (value.length === 0) return '—'
    const shown = value.slice(0, 6).map((item) => compound(key, item))
    return shown.join('; ') + (value.length > 6 ? `; and ${value.length - 6} more` : '')
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).filter(([k]) => !HIDDEN_KEYS.has(k))
    // A month/amount pair reads best as "Feb 2026: $75,000".
    if (entries.length === 2 && entries.some(([k]) => k === 'month') && entries.some(([k]) => k !== 'month')) {
      const month = entries.find(([k]) => k === 'month')![1]
      const [otherKey, other] = entries.find(([k]) => k !== 'month')!
      return `${scalar('month', month)}: ${scalar(otherKey, other)}`
    }
    return entries.map(([k, v]) => `${humanize(k)} ${compound(k, v)}`).join(', ')
  }
  return scalar(key, value)
}

export function evidenceRows(evidence: Record<string, unknown>): EvidenceRow[] {
  return Object.entries(evidence)
    .filter(([key]) => !HIDDEN_KEYS.has(key))
    .map(([key, value]) => ({ label: humanize(key), value: compound(key, value) }))
}

/** Tool names the investigator used, in plain words, for the "How this was checked" list. */
const TOOL_LABEL: Record<string, string> = {
  get_project_history: 'Read the project’s change history',
  get_project_revenue: 'Read the project’s monthly revenue',
  get_snapshots: 'Compared saved snapshots',
  get_snapshot_comparison: 'Compared saved snapshots',
  find_hubspot_deals: 'Looked for related HubSpot deals',
  get_hubspot_deal: 'Read a HubSpot deal',
  get_related_projects: 'Looked for related projects',
}

export function describeStep(tool: string): string {
  return TOOL_LABEL[tool] ?? humanize(tool)
}
