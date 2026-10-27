export type Owner = { role: string; code: string | null; name: string }

export type Project = {
  id: number
  source_row_id: string
  customer: string
  name: string
  status: string
  practice: string | null
  region: string | null
  industry: string | null
  country: string | null
  billing_type: string | null
  category: string | null
  client_partner: Owner | null
  ae: Owner | null
  commercial_owner: Owner | null
  total_revenue: string
  attention_count: number
  last_updated: string | null
  monthly: Record<string, string> | null
}

type ProjectPage = { items: Project[]; page: number; total_pages: number; total_items: number }

export type MonthColumn = {
  month: string
  label: string
  fiscal_year: string
  fiscal_period: number
  is_closed: boolean
}

export type MonthsResponse = { as_of: string | null; months: MonthColumn[] }

export type TotalsResponse = {
  months: { month: string; total: string; project_count: number }[]
  fiscal_years: { fiscal_year: string; total: string; months_with_data: number }[]
  grand_total: string
  project_count: number
}

export type ImportIssue = {
  severity: string
  code: string
  row_number: number | null
  source_row_id: string | null
  message: string
  row_rejected: boolean
}

export type ImportRun = {
  id: number
  source_file: string
  source_as_of: string | null
  status: string
  rows_read: number
  rows_rejected: number
  warnings: number
  reconciled: boolean | null
  issues: ImportIssue[]
  reconciliation: { month: string; variance: string; matches: boolean }[]
}

export type ProjectDetail = Project & {
  source_row_number: number | null
  last_updated_by: string | null
  owners: Owner[]
  monthly_revenue: { month: string; fiscal_year: string; amount: string; source_cell: string | null }[]
  attention: { code: string; message: string }[]
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { signal })
  if (!response.ok) throw new ApiError(response.status, `Request failed (${response.status})`)
  return (await response.json()) as T
}

export const fetchMonths = (signal?: AbortSignal) => get<MonthsResponse>('/v1/months', signal)
export const fetchTotals = (signal?: AbortSignal) =>
  get<TotalsResponse>('/v1/revenue/monthly-totals', signal)
export const fetchProject = (id: number, signal?: AbortSignal) =>
  get<ProjectDetail>(`/v1/projects/${id}`, signal)

/** The latest completed import, or null when nothing has been imported yet. */
export async function fetchLatestImport(signal?: AbortSignal): Promise<ImportRun | null> {
  try {
    return await get<ImportRun>('/v1/imports/latest', signal)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

/** Every project with its monthly amounts, in the server's priority order. */
export async function fetchAllProjects(signal?: AbortSignal): Promise<Project[]> {
  const projects: Project[] = []
  let page = 1
  for (;;) {
    const body = await get<ProjectPage>(
      `/v1/projects?include_revenue=true&page_size=500&page=${page}`,
      signal,
    )
    projects.push(...body.items)
    if (page >= body.total_pages) return projects
    page += 1
  }
}
