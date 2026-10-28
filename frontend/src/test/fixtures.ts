import type {
  AccuracyReport,
  Finding,
  FindingDetail,
  Health,
  Investigation,
  ActualMonth,
  AuthStatus,
  Confirmation,
  DealDetail,
  DealSummary,
  FilterOptions,
  ForecastTotals,
  HistoryItem,
  ImportRun,
  MonthColumn,
  Project,
  ReconciliationReport,
  Snapshot,
  SnapshotList,
  SnapshotTotals,
  Stage,
  SyncStatus,
  TotalsResponse,
  UploadSummary,
} from '../api'

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
  can_edit: false,
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
    can_edit: true,
    monthly: { '2026-10-01': '1250.50' },
  },
  {
    ...base,
    id: 2,
    can_edit: false,
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

export const signedIn: AuthStatus = {
  authenticated: true,
  user: {
    id: 7,
    name: 'Avery Chen',
    email: 'avery@corp.test',
    roles: ['Client Partner'],
    can_edit_all: false,
  },
  csrf_token: 'csrf-token-123',
  csrf_header: 'X-CSRF-Token',
}

export const signedOut: AuthStatus = {
  authenticated: false,
  user: null,
  csrf_token: null,
  csrf_header: null,
}

export const history: HistoryItem[] = [
  {
    id: 12,
    created_at: '2026-10-20T15:30:00Z',
    field: 'revenue',
    month: '2026-10-01',
    change_type: 'update',
    old_value: '1000.00',
    new_value: '1250.50',
    reason: 'Client added scope',
    actor_name: 'Avery Chen',
    from_import: false,
    post_snapshot: false,
  },
  {
    id: 1,
    created_at: '2026-10-01T12:00:00Z',
    field: 'project',
    month: null,
    change_type: 'create',
    old_value: null,
    new_value: 'R1003',
    reason: null,
    actor_name: 'excel-import',
    from_import: true,
    post_snapshot: false,
  },
]

export type Handler = (request: { body: unknown; headers: Record<string, string> }) => Response
export type Routes = Record<string, Handler>

/** Routes mocked fetch calls by "METHOD /path" or "/path" (GET); the query string is ignored. */
export function mockApi(routes: Routes) {
  return (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input).replace(/\?.*$/, '')
    const method = (init?.method ?? 'GET').toUpperCase()
    const handler = routes[`${method} ${path}`] ?? (method === 'GET' ? routes[path] : undefined)
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    const headers = (init?.headers ?? {}) as Record<string, string>
    return Promise.resolve(handler ? handler({ body, headers }) : json({ detail: 'Not Found' }, 404))
  }
}

const detail = {
  ...projects[0],
  source_row_number: 5,
  last_updated_by: 'Shah',
  owners: [{ role: 'ae', code: 'Shah', name: 'Riley Shah' }],
  monthly_revenue: [
    {
      month: '2026-10-01',
      fiscal_year: 'FY27',
      amount: '1250.50',
      baseline_amount: '1000.00',
      edited: true,
      source_cell: 'Forecast!U5',
    },
  ],
  attention: [{ code: 'missing_client_partner', message: 'No Client Partner' }],
  can_view_history: true,
}

// ---- Pipeline fixtures ----------------------------------------------------------------------

export const forecast: ForecastTotals = {
  months: [
    { month: '2026-10-01', fiscal_year: 'FY27', fiscal_period: 7, committed: '3000.00', weighted: '0.00', total: '3000.00' },
    { month: '2026-11-01', fiscal_year: 'FY27', fiscal_period: 8, committed: '6000.00', weighted: '10000.00', total: '16000.00' },
  ],
  fiscal_years: [{ fiscal_year: 'FY27', committed: '9000.00', weighted: '10000.00', total: '19000.00' }],
  committed_total: '9000.00',
  weighted_total: '10000.00',
  combined_total: '19000.00',
  weighted_deals: 2,
  unphased: { deals: 1, weighted_amount: '596000.00' },
  outside_window_weighted: '40000.00',
  filters_applied: false,
}

const dealBase = {
  company_id: '9000001',
  owner_code: 'Shah',
  close_date: '2026-12-01T10:30:00Z',
  practice: 'APPIAN',
  region: 'NA',
  industry: null,
  linked_project: null,
  draft: null,
  phaseable: true,
  unphased_reason: null,
  counts_in_forecast: true,
}

export const deals: DealSummary[] = [
  {
    ...dealBase,
    id: 1,
    hubspot_deal_id: '60866525874',
    name: 'Pembrook - Appian Renewal',
    company: 'Pembrook Housing Group',
    owner_name: 'Avery Chen',
    stage: 'Negotiation/Review',
    probability: '0.6000',
    amount: '812000.0000',
    weighted_amount: '487200.00',
    expected_start: '2026-10-01',
    duration_months: 12,
    lifecycle: 'weighted',
    status: 'weighted',
    findings: 1,
  },
  {
    ...dealBase,
    id: 2,
    hubspot_deal_id: '65394918517',
    name: 'Brightharbor - Claims AI Pilot',
    company: 'Brightharbor Assurance',
    owner_name: 'Riley Shah',
    stage: 'Closed Won',
    probability: '1.0000',
    amount: '186000.0000',
    weighted_amount: '186000.00',
    expected_start: '2026-11-02',
    duration_months: 6,
    lifecycle: 'closed_won',
    status: 'awaiting_confirmation',
    findings: 0,
    linked_project: { id: 55, source_row_id: 'HS-65394918517', name: 'Brightharbor - Claims AI Pilot', status: 'Draft' },
    draft: {
      project_id: 55,
      owner_name: 'Riley Shah',
      requested_at: '2026-10-01T09:00:00Z',
      due_at: '2026-10-05T09:00:00Z',
      overdue: true,
      confirmed_at: null,
      possible_duplicates: [],
    },
  },
  {
    ...dealBase,
    id: 3,
    hubspot_deal_id: '45297778568',
    name: 'Brambleton Capital - Annual Services',
    company: 'Brambleton Capital',
    owner_name: 'Jamie Lowe',
    stage: 'Contracting',
    probability: '0.8000',
    amount: '745000.0000',
    weighted_amount: '596000.00',
    expected_start: null,
    duration_months: null,
    lifecycle: 'weighted',
    status: 'weighted',
    phaseable: false,
    unphased_reason: 'missing_start_or_duration',
    findings: 2,
  },
]

export function detailOf(deal: DealSummary, over: Partial<DealDetail> = {}): DealDetail {
  return {
    ...deal,
    monthly: [
      { month: '2026-11-01', amount: '31000.00' },
      { month: '2026-12-01', amount: '31000.00' },
    ],
    outside_window: '0.00',
    findings_list: [{ severity: 'warning', code: 'stale_close_date', message: 'Open deal with a close date in the past.' }],
    events: [
      {
        id: 9,
        event_type: 'draft_created',
        created_at: '2026-10-01T09:00:00Z',
        actor_label: 'hubspot-sync',
        from_value: null,
        to_value: null,
        message: 'Closed Won: draft project created; confirmation requested',
      },
    ],
    schedule_check: null,
    can_confirm: false,
    ...over,
  }
}

export const draftDetail = detailOf(deals[1], {
  can_confirm: true,
  schedule_check: {
    deal_amount: '186000.0000',
    schedule_total: '186000.00',
    outside_window: '0.00',
    variance_ratio: '0.0000',
    exceeds_threshold: false,
  },
})

export const syncOk: SyncStatus = {
  last_run: null,
  last_success: {
    id: 4,
    status: 'completed',
    source: 'workbook',
    source_ref: 'HubSpot.xlsx',
    started_at: '2026-10-01T09:00:00Z',
    finished_at: '2026-10-01T09:00:05Z',
    deals_seen: 53,
    deals_created: 0,
    deals_updated: 0,
    drafts_created: 0,
    lines_retired: 0,
    findings_count: 13,
    error_message: null,
    triggered_by_label: 'system',
  },
  stale: false,
  reason: null,
  stale_since: null,
  stale_after_minutes: 60,
  can_sync: false,
  configured_source: 'workbook',
}

export const confirmations: Confirmation[] = [
  {
    project_id: 55,
    project_name: 'Brightharbor - Claims AI Pilot',
    customer: 'Brightharbor Assurance',
    deal_id: 2,
    hubspot_deal_id: '65394918517',
    owner_name: 'Riley Shah',
    amount: '186000.0000',
    schedule_total: '186000.00',
    requested_at: '2026-10-01T09:00:00Z',
    due_at: '2026-10-05T09:00:00Z',
    overdue: true,
    has_possible_duplicates: false,
    can_confirm: true,
  },
]

export const filterOptions: FilterOptions = {
  practices: ['APPIAN', 'CRM'],
  regions: ['NA', 'UK'],
  industries: ['Insurance'],
  categories: ['EE', 'EN', 'NN'],
  customers: ['Brightharbor Assurance', 'Pembrook Housing Group'],
  owners: [
    { code: 'Shah', name: 'Riley Shah' },
    { code: 'AC', name: 'Avery Chen' },
  ],
}

export const stages: Stage[] = [
  { id: 1, name: 'Presentation', probability: '0.2000', kind: 'open', in_forecast: true, position: 0, source: 'workbook' },
  { id: 2, name: 'Negotiation/Review', probability: '0.6000', kind: 'open', in_forecast: true, position: 1, source: 'workbook' },
  { id: 3, name: 'Closed Lost', probability: '0.0000', kind: 'lost', in_forecast: false, position: 2, source: 'workbook' },
]

export const adminMe: AuthStatus = {
  ...signedIn,
  user: { id: 1, name: 'Ada Admin', email: 'ada@corp.test', roles: ['Administrator'], can_edit_all: true },
}

export const pipelineRoutes: Routes = {
  '/api/v1/forecast/monthly': () => json(forecast),
  '/api/v1/pipeline/deals': () =>
    json({ items: deals, page: 1, page_size: 500, total_items: 3, total_pages: 1, weighted_total: '1269200.00' }),
  '/api/v1/pipeline/deals/1': () => json(detailOf(deals[0])),
  '/api/v1/pipeline/deals/2': () => json(draftDetail),
  '/api/v1/pipeline/sync/status': () => json(syncOk),
  '/api/v1/pipeline/confirmations': () => json(confirmations),
  '/api/v1/pipeline/filter-options': () => json(filterOptions),
  '/api/v1/pipeline/stages': () => json(stages),
}

// ---- Snapshot, actuals and accuracy fixtures --------------------------------------------------

export const financeMe: AuthStatus = {
  ...signedIn,
  user: { id: 3, name: 'Fin Ance', email: 'fin@corp.test', roles: ['Finance'], can_edit_all: false },
}
export const croMe: AuthStatus = {
  ...signedIn,
  user: { id: 4, name: 'Casey CRO', email: 'cro@corp.test', roles: ['CRO / Sales Leadership'], can_edit_all: true },
}

export const snapshot1: Snapshot = {
  id: 1,
  label: '2026-11 scheduled',
  period_month: '2026-11-01',
  kind: 'scheduled',
  taken_at: '2026-11-02T09:00:00Z',
  taken_by_label: 'scheduled job',
  note: null,
  as_of: '2026-10-01',
  import_run_id: 1,
  sync_run_id: 2,
  stage_probabilities: [],
  committed_total: '18000.00',
  weighted_total: '60000.00',
  combined_total: '78000.00',
  unphased_deals: 1,
  unphased_weighted: '596000.00',
  outside_window_weighted: '0.00',
  project_count: 3,
  deal_count: 2,
  line_count: 12,
  checksum: 'abcdef0123456789'.repeat(4),
}
export const snapshot2: Snapshot = {
  ...snapshot1,
  id: 2,
  label: '2026-11 manual (03 Nov 10:00 UTC)',
  kind: 'manual',
  taken_by_label: 'Fin Ance',
  note: 'Board pack',
  combined_total: '80000.00',
}

export const snapshotList: SnapshotList = {
  items: [snapshot2, snapshot1],
  next_scheduled: '2026-12-01',
  scheduled_taken_this_month: true,
  last_job: {
    id: 5,
    job: 'monthly_snapshot',
    started_at: '2026-11-02T09:00:00Z',
    finished_at: '2026-11-02T09:00:02Z',
    status: 'completed',
    message: 'Took the November 2026 snapshot #1.',
    snapshot_id: 1,
  },
  can_take: false,
}

export const snapshotTotals: SnapshotTotals = {
  snapshot: snapshot1,
  months: forecast.months,
  fiscal_years: forecast.fiscal_years.map((y) => ({ ...y })),
  committed_total: '18000.00',
  weighted_total: '60000.00',
  combined_total: '78000.00',
  unphased: { deals: 1, weighted_amount: '596000.00' },
  outside_window_weighted: '0.00',
  filters_applied: false,
}

export const reconciliation: ReconciliationReport = {
  filename: 'october.csv',
  rows_total: 3,
  rows_rejected: 0,
  rows_new: 3,
  rows_replaced: 0,
  rows_unchanged: 0,
  total_amount: '3000.00',
  project_level_rows: 2,
  customer_level_rows: 1,
  customers: 3,
  ignored_columns: ['Notes'],
  errors: [],
  errors_total: 0,
  warnings: [],
  months: [{ month: '2026-10-01', rows: 3, actual_total: '3000.00', committed_forecast_now: '3000.00' }],
}

export const uploads: UploadSummary[] = [
  {
    id: 2,
    filename: 'bad.csv',
    status: 'rejected',
    uploaded_at: '2026-11-04T10:00:00Z',
    uploaded_by_label: 'Fin Ance',
    rows_total: 4,
    rows_accepted: 0,
    rows_rejected: 2,
    rows_unchanged: 0,
    rows_replaced: 0,
    total_amount: '0.00',
    months: ['2026-10-01'],
  },
  {
    id: 1,
    filename: 'october.csv',
    status: 'completed',
    uploaded_at: '2026-11-03T10:00:00Z',
    uploaded_by_label: 'Fin Ance',
    rows_total: 3,
    rows_accepted: 3,
    rows_rejected: 0,
    rows_unchanged: 0,
    rows_replaced: 0,
    total_amount: '3000.00',
    months: ['2026-10-01'],
  },
]

export const actualMonths: ActualMonth[] = [
  { month: '2026-10-01', fiscal_year: 'FY27', actual_total: '3000.00', committed_forecast: '3000.00' },
]

const noLag = { snapshot_id: null, snapshot_label: null, snapshot_taken_at: null, forecast: null, committed: null, weighted: null, variance: null, variance_percent: null }
export const accuracy: AccuracyReport = {
  tolerance_percent: '5',
  target_lag: 3,
  months: [
    {
      month: '2026-10-01',
      fiscal_year: 'FY27',
      actual: '3000.00',
      lags: [
        { lag: 1, status: 'ok', snapshot_id: 3, snapshot_label: '2026-09 scheduled', snapshot_taken_at: '2026-09-01T09:00:00Z', forecast: '3200.00', committed: '3200.00', weighted: '0.00', variance: '200.00', variance_percent: '6.67', flagged: true },
        { lag: 2, status: 'no_snapshot', ...noLag, flagged: false },
        { lag: 3, status: 'ok', snapshot_id: 1, snapshot_label: '2026-07 scheduled', snapshot_taken_at: '2026-07-01T09:00:00Z', forecast: '3000.00', committed: '3000.00', weighted: '0.00', variance: '0.00', variance_percent: '0.00', flagged: false },
      ],
    },
  ],
  totals: [
    { lag: 1, months_compared: 1, months_flagged: 1, forecast: '3200.00', actual: '3000.00', variance: '200.00', variance_percent: '6.67', flagged: true },
    { lag: 2, months_compared: 0, months_flagged: 0, forecast: '0.00', actual: '0.00', variance: '0.00', variance_percent: null, flagged: false },
    { lag: 3, months_compared: 1, months_flagged: 0, forecast: '3000.00', actual: '3000.00', variance: '0.00', variance_percent: '0.00', flagged: false },
  ],
}

export const reportingRoutes: Routes = {
  '/api/v1/snapshots': () => json(snapshotList),
  '/api/v1/snapshots/1': () => json(snapshotTotals),
  '/api/v1/snapshots/1/verify': () => json({ snapshot_id: 1, intact: true, checksum: snapshot1.checksum }),
  '/api/v1/actuals/uploads': () => json(uploads),
  '/api/v1/actuals/months': () => json(actualMonths),
  '/api/v1/accuracy': () => json(accuracy),
}

// ---- Quality fixtures -----------------------------------------------------------------------------

export const finding1: Finding = {
  id: 11,
  rule_code: 'mom_anomaly',
  rule_label: 'Unusual month-over-month revenue movement',
  severity: 'warning',
  title: 'Unusual revenue rise: Data Platform Co - Data Platform Build',
  summary: 'R1008 moves $30,000.00 to $51,000.00 in Mar 2027 (70.0%) with no reason recorded.',
  status: 'open',
  financial_impact: '21000.00',
  routed_to_id: 7,
  routed_to_name: 'Avery Chen',
  project_id: 1,
  opportunity_id: null,
  related_project_ids: [],
  created_at: '2026-10-27T09:00:00Z',
  last_seen_at: '2026-10-27T09:00:00Z',
  reviewed_by_name: null,
  reviewed_at: null,
  review_reason: null,
  investigation_status: null,
  awaiting_review: false,
  can_review: true,
}
export const finding2: Finding = {
  ...finding1,
  id: 12,
  rule_code: 'missing_required',
  rule_label: 'Missing required fields',
  severity: 'error',
  title: 'Missing owner: Ironleaf Manufacturing - Plant Systems',
  summary: 'R1014 is missing: owner.',
  financial_impact: '120000.00',
  routed_to_id: null,
  routed_to_name: null,
  project_id: 2,
  can_review: false,
}
export const finding3: Finding = {
  ...finding1,
  id: 13,
  rule_code: 'closed_won_missing',
  rule_label: 'Closed Won deal not in the committed forecast',
  title: 'Closed Won not committed: Brightharbor - Claims AI Pilot',
  summary: 'Deal 65394918517 is Closed Won ($186,000.00) but its draft has not been confirmed.',
  project_id: 55,
  opportunity_id: 2,
  financial_impact: '186000.00',
  investigation_status: 'completed',
  awaiting_review: true,
}

export const completedInvestigation: Investigation = {
  id: 5,
  finding_id: 11,
  status: 'completed',
  mode: 'deterministic',
  model: null,
  requested_by_label: 'Avery Chen',
  started_at: '2026-10-27T10:00:00Z',
  finished_at: '2026-10-27T10:00:02Z',
  fallback_reason: 'No Anthropic API key is configured.',
  error_message: null,
  steps: [
    { tool: 'get_project_history', arguments: { project_id: 1 }, refs: ['project:1'], outcome: 'ok' },
    { tool: 'get_hubspot_deals', arguments: { company: 'Data Platform Co' }, refs: ['deal:9'], outcome: 'ok' },
  ],
  result: {
    summary: 'R1008 has an unexplained 70.0% movement in 2027-03; the records confirm the figures but not why they changed.',
    confidence: 'medium',
    causes: [
      { statement: 'Revenue moves from 30000.00 to 51000.00 in 2027-03 (70.0%).', status: 'confirmed', evidence_refs: ['project:1'] },
      { statement: 'A Closed Lost deal exists for the same customer; nothing links it to this movement.', status: 'hypothesis', evidence_refs: ['deal:9', 'project:1'] },
      { statement: 'The author of the change cannot be determined.', status: 'insufficient_evidence', evidence_refs: [] },
    ],
    next_steps: [{ action: 'Ask the row owner what changed and record a reason code.', who: 'Avery Chen (client partner)' }],
    limitations: ['Fewer than two snapshots exist.'],
    financial_impact: { amount: '21000.00', basis: 'the size of the largest month-over-month movement', source: 'deterministic' },
  },
}

export const findingDetail: FindingDetail = {
  ...finding1,
  evidence: { project: { row_id: 'R1008', customer: 'Data Platform Co' }, billing_type: 'T&M' },
  suggested_fix: 'Ask the owner what changed. If it is intended, record the reason when editing the month.',
  projects: [{ project_id: 1, row_id: 'R1008', customer: 'Data Platform Co', name: 'Data Platform Build', status: 'Active' }],
  deal: null,
  events: [
    { id: 1, event_type: 'created', created_at: '2026-10-27T09:00:00Z', actor_label: 'qa-engine', message: 'Raised by the mom_anomaly check: Unusual revenue rise', details: {} },
  ],
  investigations: [],
  can_investigate: true,
  ai_available: false,
  ai_unavailable_reason: 'No Anthropic API key is configured.',
}

export const health: Health = {
  status: 'red',
  status_reasons: ['1 open error finding(s).', '2 open warning(s).'],
  open_total: 3,
  open_by_severity: { error: 1, warning: 2 },
  open_by_rule: { mom_anomaly: 1, missing_required: 1, closed_won_missing: 1 },
  revenue_at_stake: '327000.00',
  unassigned_open: 1,
  awaiting_review: 1,
  owners: [
    { user_id: 7, name: 'Avery Chen', open_findings: 2, revenue_at_stake: '207000.00', oldest_days: 3 },
    { user_id: null, name: 'Unassigned (needs an owner)', open_findings: 1, revenue_at_stake: '120000.00', oldest_days: 0 },
  ],
  metrics: [
    { key: 'open', label: 'Open findings', value: '3', formula: 'count of findings with status open' },
    { key: 'triage_rate', label: 'Triaged', value: '0%', formula: '(accepted + dismissed) / (accepted + dismissed + open)' },
  ],
  last_run: {
    id: 4, trigger: 'scheduled', status: 'completed', started_at: '2026-10-27T02:00:00Z', finished_at: '2026-10-27T02:00:03Z',
    triggered_by_label: 'nightly job', findings_new: 3, findings_seen: 0, findings_reopened: 0, findings_resolved: 0,
    findings_open: 3, rule_counts: {}, error_message: null,
  },
  last_success: null,
  checks_overdue: false,
  as_of: '2026-10-27',
  can_run: false,
}

export const qualityRoutes: Routes = {
  '/api/v1/qa/health': () => json(health),
  '/api/v1/qa/findings': () => json({ items: [finding2, finding1, finding3], total_items: 3 }),
  '/api/v1/qa/findings/11': () => json(findingDetail),
  '/api/v1/qa/findings/12': () => json({ ...findingDetail, ...finding2, projects: [], can_investigate: false }),
  '/api/v1/qa/findings/13': () => json({ ...findingDetail, ...finding3, projects: [], deal: { id: 1, hubspot_deal_id: '65394918517', name: 'Pembrook - Appian Renewal', stage: 'Closed Won', lifecycle: 'closed_won', amount: '186000.00', company: 'Brightharbor' } }),
}

export const okRoutes: Routes = {
  '/api/health': () => json({ status: 'ok', service: 'chosen-one-api' }),
  '/api/auth/me': () => json(signedIn),
  '/api/v1/projects': () =>
    json({ items: projects, page: 1, page_size: 500, total_items: 2, total_pages: 1 }),
  '/api/v1/months': () => json({ as_of: '2026-10-01', months }),
  '/api/v1/revenue/monthly-totals': () => json(totals),
  '/api/v1/imports/latest': () => json(latestImport),
  '/api/v1/projects/1': () => json(detail),
  '/api/v1/projects/1/history': () => json({ items: history, total_items: history.length }),
  '/api/v1/projects/2': () =>
    json({
      ...projects[1],
      source_row_number: 3,
      last_updated_by: 'AC',
      owners: [],
      monthly_revenue: [],
      attention: [],
      can_view_history: false,
    }),
  'POST /api/auth/logout': () => json({ authenticated: false }),
  ...pipelineRoutes,
  ...reportingRoutes,
  ...qualityRoutes,
}
