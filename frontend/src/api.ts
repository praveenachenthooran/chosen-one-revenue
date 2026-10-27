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
  can_edit: boolean
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
  monthly_revenue: {
    month: string
    fiscal_year: string
    amount: string
    baseline_amount: string | null
    edited: boolean
    source_cell: string | null
  }[]
  attention: { code: string; message: string }[]
  can_view_history: boolean
}

export type AuthUser = {
  id: number
  name: string
  email: string | null
  roles: string[]
  can_edit_all: boolean
}

export type AuthStatus = {
  authenticated: boolean
  user: AuthUser | null
  csrf_token: string | null
  csrf_header: string | null
}

export type EditResult = {
  project_id: number
  month: string
  amount: string
  previous_amount: string | null
  changed: boolean
  project_total: string
  revision_id: number | null
}

export type HistoryItem = {
  id: number
  created_at: string
  field: string
  month: string | null
  change_type: string
  old_value: string | null
  new_value: string | null
  reason: string | null
  actor_name: string
  from_import: boolean
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly currentAmount: string | null
  constructor(status: number, message: string, code: string | null = null, currentAmount: string | null = null) {
    super(message)
    this.status = status
    this.code = code
    this.currentAmount = currentAmount
  }
}

// The CSRF token for state-changing requests; handed out by /auth/me for the current session.
let csrf: { header: string; token: string } | null = null

async function failure(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    const detail = body.detail
    if (detail && typeof detail === 'object' && 'message' in detail) {
      const d = detail as { message: string; code?: string; current_amount?: string | null }
      return new ApiError(response.status, d.message, d.code ?? null, d.current_amount ?? null)
    }
    if (typeof detail === 'string') return new ApiError(response.status, detail)
  } catch {
    // not JSON; fall through
  }
  return new ApiError(response.status, `Request failed (${response.status})`)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: 'same-origin', ...init })
  if (!response.ok) throw await failure(response)
  return (await response.json()) as T
}

const get = <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal })

function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (csrf) headers[csrf.header] = csrf.token
  return request<T>(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export async function fetchAuth(signal?: AbortSignal): Promise<AuthStatus> {
  const status = await get<AuthStatus>('/auth/me', signal)
  csrf = status.csrf_header && status.csrf_token ? { header: status.csrf_header, token: status.csrf_token } : null
  return status
}

export async function signOut(): Promise<void> {
  await send('POST', '/auth/logout')
  csrf = null
}

/** Set one month of the current forecast. `expected` is what the editor last saw (null = blank). */
export const saveMonth = (
  projectId: number,
  month: string,
  amount: string,
  expected: string | null,
  reason?: string,
) =>
  send<EditResult>('PUT', `/v1/projects/${projectId}/revenue/${month}`, {
    amount,
    expected_amount: expected,
    reason: reason || null,
  })

export const fetchHistory = (projectId: number, signal?: AbortSignal) =>
  get<{ items: HistoryItem[]; total_items: number }>(`/v1/projects/${projectId}/history`, signal)

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
