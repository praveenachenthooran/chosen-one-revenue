import type { ImportRun, MonthColumn, Project, TotalsResponse } from '../api'

export const months: MonthColumn[] = [
  { month: '2026-09-01', label: 'Sep 2026', fiscal_year: 'FY27', fiscal_period: 6, is_closed: true },
  { month: '2026-10-01', label: 'Oct 2026', fiscal_year: 'FY27', fiscal_period: 7, is_closed: false },
]

const base = {
  practice: 'CRM',
  region: 'NA',
  industry: 'Insurance',
  country: 'Canada',
  billing_type: 'T&M',
  category: 'EE',
  ae: null,
  commercial_owner: null,
  last_updated: '2026-09-14',
}

export const projects: Project[] = [
  {
    ...base,
    id: 1,
    source_row_id: 'R1003',
    customer: 'Cedarfall Energy',
    name: 'Data Platform Build',
    status: 'Active',
    client_partner: null,
    total_revenue: '1250.50',
    attention_count: 1,
    monthly: { '2026-10-01': '1250.50' },
  },
  {
    ...base,
    id: 2,
    source_row_id: 'R1001',
    customer: 'Kestrel Life',
    name: 'Policy Admin',
    status: 'Completed',
    client_partner: { role: 'client_partner', code: 'AC', name: 'Avery Chen' },
    total_revenue: '80000.00',
    attention_count: 0,
    monthly: { '2026-09-01': '80000.00' },
  },
]

export const totals: TotalsResponse = {
  months: [
    { month: '2026-09-01', total: '80000.00', project_count: 1 },
    { month: '2026-10-01', total: '1250.50', project_count: 1 },
  ],
  fiscal_years: [{ fiscal_year: 'FY27', total: '81250.50', months_with_data: 2 }],
  grand_total: '81250.50',
  project_count: 2,
}

export const latestImport: ImportRun = {
  id: 6,
  source_file: 'Chosen_One_Dummy_Data_FY27.xlsx',
  source_as_of: '2026-10-01',
  status: 'completed',
  rows_read: 2,
  rows_rejected: 0,
  warnings: 1,
  reconciled: true,
  issues: [
    {
      severity: 'warning',
      code: 'missing_client_partner',
      row_number: 5,
      source_row_id: 'R1003',
      message: 'No Client Partner (blank or N/A); every project needs an owner',
      row_rejected: false,
    },
  ],
  reconciliation: [{ month: '2026-10-01', variance: '0.00', matches: true }],
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Routes mocked fetch calls by URL path (query string ignored). */
export function mockApi(routes: Record<string, () => Response>) {
  return (input: RequestInfo | URL): Promise<Response> => {
    const path = String(input).replace(/\?.*$/, '')
    const route = routes[path]
    return Promise.resolve(route ? route() : json({ detail: 'Not Found' }, 404))
  }
}

export const okRoutes: Record<string, () => Response> = {
  '/api/health': () => json({ status: 'ok', service: 'chosen-one-api' }),
  '/api/v1/projects': () =>
    json({ items: projects, page: 1, page_size: 500, total_items: 2, total_pages: 1 }),
  '/api/v1/months': () => json({ as_of: '2026-10-01', months }),
  '/api/v1/revenue/monthly-totals': () => json(totals),
  '/api/v1/imports/latest': () => json(latestImport),
  '/api/v1/projects/1': () =>
    json({
      ...projects[0],
      source_row_number: 5,
      last_updated_by: 'Shah',
      owners: [{ role: 'ae', code: 'Shah', name: 'Riley Shah' }],
      monthly_revenue: [
        { month: '2026-10-01', fiscal_year: 'FY27', amount: '1250.50', source_cell: 'Forecast!U5' },
      ],
      attention: [{ code: 'missing_client_partner', message: 'No Client Partner' }],
    }),
}
