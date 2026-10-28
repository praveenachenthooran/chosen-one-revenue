import type { ProjectRow } from './status'

export type FilterField = 'status' | 'customer' | 'owner' | 'practice' | 'region' | 'month'
export type ProjectFilters = { q: string; status: string; customer: string; owner: string; practice: string; region: string; month: string }
export const NO_FILTERS: ProjectFilters = { q: '', status: '', customer: '', owner: '', practice: '', region: '', month: '' }

const ownerNames = (r: ProjectRow) => [r.project.client_partner?.name, r.project.commercial_owner?.name].filter((n): n is string => Boolean(n))

export function applyFilters(rows: ProjectRow[], f: ProjectFilters): ProjectRow[] {
  const q = f.q.trim().toLowerCase()
  return rows.filter((r) => {
    const p = r.project
    if (q && !`${p.customer} ${p.name}`.toLowerCase().includes(q)) return false
    if (f.status && r.status !== f.status) return false
    if (f.customer && p.customer !== f.customer) return false
    if (f.owner && !ownerNames(r).includes(f.owner)) return false
    if (f.practice && p.practice !== f.practice) return false
    if (f.region && p.region !== f.region) return false
    if (f.month && !(Number(p.monthly?.[f.month] ?? 0) > 0)) return false
    return true
  })
}

export const ownerNamesOf = ownerNames
