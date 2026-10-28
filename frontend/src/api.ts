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
  post_snapshot: boolean
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly currentAmount: string | null
  /** The whole error detail, for errors that carry more than a message (an upload's report). */
  readonly detail: Record<string, unknown> | null
  constructor(
    status: number,
    message: string,
    code: string | null = null,
    currentAmount: string | null = null,
    detail: Record<string, unknown> | null = null,
  ) {
    super(message)
    this.status = status
    this.code = code
    this.currentAmount = currentAmount
    this.detail = detail
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
      return new ApiError(response.status, d.message, d.code ?? null, d.current_amount ?? null, detail as Record<string, unknown>)
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

// ---- Pipeline (Milestone 3) ---------------------------------------------------------------

export type ForecastMonth = {
  month: string
  fiscal_year: string
  fiscal_period: number
  committed: string
  weighted: string
  total: string
}

export type ForecastTotals = {
  months: ForecastMonth[]
  fiscal_years: { fiscal_year: string; committed: string; weighted: string; total: string }[]
  committed_total: string
  weighted_total: string
  combined_total: string
  weighted_deals: number
  unphased: { deals: number; weighted_amount: string }
  outside_window_weighted: string
  filters_applied: boolean
}

export type DealStatus =
  | 'weighted'
  | 'awaiting_confirmation'
  | 'closed_won_pending'
  | 'linked'
  | 'closed_lost'
  | 'excluded'

export type DraftInfo = {
  project_id: number
  owner_name: string | null
  requested_at: string
  due_at: string
  overdue: boolean
  confirmed_at: string | null
  possible_duplicates: { project_id: number; source_row_id: string; name: string; status: string }[]
}

export type DealSummary = {
  id: number
  hubspot_deal_id: string
  name: string
  company: string | null
  company_id: string | null
  owner_name: string | null
  owner_code: string | null
  stage: string
  probability: string | null
  amount: string | null
  weighted_amount: string
  expected_start: string | null
  duration_months: number | null
  close_date: string | null
  practice: string | null
  region: string | null
  industry: string | null
  lifecycle: string
  status: DealStatus
  counts_in_forecast: boolean
  phaseable: boolean
  unphased_reason: string | null
  linked_project: { id: number; source_row_id: string; name: string; status: string } | null
  draft: DraftInfo | null
  findings: number
}

export type DealPage = {
  items: DealSummary[]
  page: number
  total_items: number
  total_pages: number
  weighted_total: string
}

export type ScheduleCheck = {
  deal_amount: string | null
  schedule_total: string
  outside_window: string
  variance_ratio: string | null
  exceeds_threshold: boolean
}

export type PipelineEvent = {
  id: number
  event_type: string
  created_at: string
  actor_label: string
  from_value: string | null
  to_value: string | null
  message: string
}

export type DealDetail = DealSummary & {
  monthly: { month: string; amount: string }[]
  outside_window: string
  findings_list: { severity: string; code: string; message: string }[]
  events: PipelineEvent[]
  schedule_check: ScheduleCheck | null
  can_confirm: boolean
}

export type SyncRun = {
  id: number
  status: string
  source: string
  source_ref: string | null
  started_at: string
  finished_at: string | null
  deals_seen: number
  deals_created: number
  deals_updated: number
  drafts_created: number
  lines_retired: number
  findings_count: number
  error_message: string | null
  triggered_by_label: string
}

export type SyncStatus = {
  last_run: SyncRun | null
  last_success: SyncRun | null
  stale: boolean
  reason: 'never' | 'failed' | 'old' | null
  stale_since: string | null
  stale_after_minutes: number
  can_sync: boolean
  configured_source: string
}

export type Confirmation = {
  project_id: number
  project_name: string
  customer: string
  deal_id: number
  hubspot_deal_id: string
  owner_name: string | null
  amount: string | null
  schedule_total: string
  requested_at: string
  due_at: string
  overdue: boolean
  has_possible_duplicates: boolean
  can_confirm: boolean
}

export type FilterOptions = {
  practices: string[]
  regions: string[]
  industries: string[]
  categories: string[]
  customers: string[]
  owners: { code: string; name: string }[]
}

export type Stage = {
  id: number
  name: string
  probability: string
  kind: 'open' | 'won' | 'lost'
  in_forecast: boolean
  position: number
  source: string
}

export type ForecastFilters = {
  owner?: string
  practice?: string
  region?: string
  industry?: string
  category?: string
  customer?: string
  q?: string
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const fetchForecast = (filters: ForecastFilters, signal?: AbortSignal) =>
  get<ForecastTotals>(`/v1/forecast/monthly${query({ ...filters })}`, signal)

export const fetchDeals = (
  filters: ForecastFilters,
  options: { status?: string; sort?: string } = {},
  signal?: AbortSignal,
) => get<DealPage>(`/v1/pipeline/deals${query({ ...filters, ...options, page_size: 500 })}`, signal)

export const fetchDeal = (id: number, signal?: AbortSignal) =>
  get<DealDetail>(`/v1/pipeline/deals/${id}`, signal)

export const fetchSyncStatus = (signal?: AbortSignal) =>
  get<SyncStatus>('/v1/pipeline/sync/status', signal)

export const triggerSync = () => send<SyncRun>('POST', '/v1/pipeline/sync')

export const fetchConfirmations = (signal?: AbortSignal) =>
  get<Confirmation[]>('/v1/pipeline/confirmations', signal)

export const fetchFilterOptions = (signal?: AbortSignal) =>
  get<FilterOptions>('/v1/pipeline/filter-options', signal)

export const fetchStages = (signal?: AbortSignal) => get<Stage[]>('/v1/pipeline/stages', signal)

export const updateStage = (id: number, body: { probability?: string; in_forecast?: boolean }) =>
  send<Stage>('PUT', `/v1/pipeline/stages/${id}`, body)

export const confirmDraft = (
  projectId: number,
  body: { acknowledge_possible_duplicate?: boolean; category?: string; industry?: string },
) =>
  send<{ project_id: number; status: string; weighted_line_retired: boolean; schedule_check: ScheduleCheck | null }>(
    'POST',
    `/v1/pipeline/drafts/${projectId}/confirm`,
    body,
  )

// ---- Snapshots, exports, actuals and accuracy (Milestone 4) -----------------------------------

export type Snapshot = {
  id: number
  label: string
  period_month: string
  kind: 'scheduled' | 'manual'
  taken_at: string
  taken_by_label: string
  note: string | null
  as_of: string | null
  import_run_id: number | null
  sync_run_id: number | null
  stage_probabilities: { name: string; probability: string; kind: string; in_forecast: boolean }[]
  committed_total: string
  weighted_total: string
  combined_total: string
  unphased_deals: number
  unphased_weighted: string
  outside_window_weighted: string
  project_count: number
  deal_count: number
  line_count: number
  checksum: string
}

export type JobRun = {
  id: number
  job: string
  started_at: string
  finished_at: string
  status: 'completed' | 'skipped' | 'failed'
  message: string
  snapshot_id: number | null
}

export type SnapshotList = {
  items: Snapshot[]
  next_scheduled: string
  scheduled_taken_this_month: boolean
  last_job: JobRun | null
  can_take: boolean
}

export type SnapshotTotals = {
  snapshot: Snapshot
  months: ForecastMonth[]
  fiscal_years: { fiscal_year: string; committed: string; weighted: string; total: string }[]
  committed_total: string
  weighted_total: string
  combined_total: string
  unphased: { deals: number; weighted_amount: string } | null
  outside_window_weighted: string | null
  filters_applied: boolean
}

export type UploadSummary = {
  id: number
  filename: string
  status: 'completed' | 'rejected'
  uploaded_at: string
  uploaded_by_label: string
  rows_total: number
  rows_accepted: number
  rows_rejected: number
  rows_unchanged: number
  rows_replaced: number
  total_amount: string
  months: string[]
}

export type RowProblem = { row: number; code: string; message: string; field: string | null }

export type ReconciliationReport = {
  filename: string
  rows_total: number
  rows_rejected: number
  rows_new?: number
  rows_replaced?: number
  rows_unchanged?: number
  total_amount: string
  project_level_rows: number
  customer_level_rows: number
  customers: number
  ignored_columns: string[]
  errors: RowProblem[]
  errors_total: number
  warnings: RowProblem[]
  months: { month: string; rows: number; actual_total: string; committed_forecast_now: string }[]
}

export type UploadResult = {
  dry_run: boolean
  status: 'completed' | 'rejected'
  upload_id: number | null
  report: ReconciliationReport
}

export type ActualMonth = { month: string; fiscal_year: string; actual_total: string; committed_forecast: string }

export type AccuracyLag = {
  lag: number
  status: 'ok' | 'no_snapshot' | 'month_not_in_snapshot'
  snapshot_id: number | null
  snapshot_label: string | null
  snapshot_taken_at: string | null
  forecast: string | null
  committed: string | null
  weighted: string | null
  variance: string | null
  variance_percent: string | null
  flagged: boolean
}

export type AccuracyReport = {
  tolerance_percent: string
  target_lag: number
  months: { month: string; fiscal_year: string; actual: string; lags: AccuracyLag[] }[]
  totals: {
    lag: number
    months_compared: number
    months_flagged: number
    forecast: string
    actual: string
    variance: string
    variance_percent: string | null
    flagged: boolean
  }[]
}

export const fetchSnapshots = (signal?: AbortSignal) => get<SnapshotList>('/v1/snapshots', signal)

export const takeSnapshot = (note: string) =>
  send<Snapshot>('POST', '/v1/snapshots', note.trim() ? { note: note.trim() } : {})

export const fetchSnapshot = (id: number, filters: ForecastFilters, signal?: AbortSignal) =>
  get<SnapshotTotals>(`/v1/snapshots/${id}${query({ ...filters })}`, signal)

export const verifySnapshot = (id: number) =>
  get<{ snapshot_id: number; intact: boolean; checksum: string }>(`/v1/snapshots/${id}/verify`)

export type ExportFormat = 'xlsx' | 'csv'
export type CsvView = 'monthly' | 'projects' | 'pipeline'

/** Where a file download comes from. Plain links keep the browser's own download handling. */
export function exportUrl(
  source: { snapshotId: number | null },
  filters: ForecastFilters,
  format: ExportFormat,
  view: CsvView = 'monthly',
): string {
  const path = source.snapshotId === null ? '/v1/forecast/export' : `/v1/snapshots/${source.snapshotId}/export`
  return `/api${path}${query({ ...filters, format, view: format === 'csv' ? view : undefined })}`
}

export const fetchUploads = (signal?: AbortSignal) => get<UploadSummary[]>('/v1/actuals/uploads', signal)
export const fetchActualMonths = (signal?: AbortSignal) => get<ActualMonth[]>('/v1/actuals/months', signal)
export const fetchAccuracy = (signal?: AbortSignal) => get<AccuracyReport>('/v1/accuracy', signal)

/** Send an actuals file as the request body. `dryRun` checks and reconciles without saving. */
export function uploadActuals(file: File, dryRun: boolean): Promise<UploadResult> {
  const headers: Record<string, string> = { 'Content-Type': 'application/octet-stream' }
  if (csrf) headers[csrf.header] = csrf.token
  return request<UploadResult>(`/v1/actuals/uploads${query({ filename: file.name, dry_run: dryRun ? 'true' : undefined })}`, {
    method: 'POST',
    headers,
    body: file,
  })
}
