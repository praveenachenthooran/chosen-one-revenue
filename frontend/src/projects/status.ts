import type { Finding, Health, Project } from '../api'

/**
 * Forecast status for the Client Partner list. Three business labels, derived only from the
 * deterministic QA findings and the project's own forecast; there is no score and nothing subjective.
 *
 *   Missing Entry  an active project with a blank forecast for the current month, or a still-detected
 *                  "missing required fields" finding.
 *   Off Track      any other still-detected finding of warning or error severity.
 *   On Track       none of the above.
 *
 * A finding counts as "still detected" while it is open, or while it was accepted or dismissed but the
 * latest successful check run still detects the same condition (the engine refreshes `last_seen_at` to
 * that run's start time). Accepting or dismissing a finding therefore never turns a project green by
 * itself: the project changes status only once the underlying condition has gone.
 */
export type ProjectStatus = 'off_track' | 'missing_entry' | 'on_track'
export type Priority = 'High' | 'Medium' | 'Low'

export const STATUS_LABEL: Record<ProjectStatus, string> = {
  off_track: 'Off Track',
  missing_entry: 'Missing Entry',
  on_track: 'On Track',
}
export const STATUS_TONE: Record<ProjectStatus, 'red' | 'amber' | 'green'> = {
  off_track: 'red',
  missing_entry: 'amber',
  on_track: 'green',
}

const ACTION_BY_RULE: Record<string, string> = {
  duplicate_project: 'Confirm which row is the real project',
  closed_won_missing: 'Add the signed deal to the forecast',
  amount_mismatch: 'Align the forecast with the HubSpot amount',
  stale_row: 'Review and update this forecast',
  mom_anomaly: 'Verify the change in monthly revenue',
  missing_required: 'Fill in the missing project details',
  deal_missing_fields: 'Add the deal’s start date and duration',
}

export type ProjectRow = {
  project: Project
  status: ProjectStatus
  priority: Priority
  /** Findings that still apply, most severe first. */
  findings: Finding[]
  /** Reviewed findings whose condition has gone: kept for the history, not counted. */
  clearedFindings: Finding[]
  action: string
  owner: string
  /** Sum of the still-detected findings' calculated impact. */
  impact: number
  awaitingReview: boolean
}

const SEVERITY_RANK: Record<string, number> = { error: 0, warning: 1, info: 2 }

/** True while the check engine still detects the condition behind this finding. */
export function stillDetected(finding: Finding, lastRunStartedAt: string | null): boolean {
  if (finding.status === 'resolved') return false
  if (finding.status === 'open') return true
  if (!finding.last_seen_at || !lastRunStartedAt) return true // cannot prove it cleared, so keep it
  return new Date(finding.last_seen_at).getTime() >= new Date(lastRunStartedAt).getTime()
}

/** Whether the forecast cell for a month is blank (no entry), as opposed to an explicit 0. */
export const isBlank = (project: Project, month: string) => {
  const value = project.monthly?.[month]
  return value === undefined || value === null || value === ''
}

export function buildRow(project: Project, allFindings: Finding[], health: Pick<Health, 'last_success'> | null, currentMonth: string): ProjectRow {
  const lastRun = health?.last_success?.started_at ?? null
  const mine = allFindings.filter((f) => f.project_id === project.id || f.related_project_ids.includes(project.id))
  const counted = mine
    .filter((f) => f.severity !== 'info' && stillDetected(f, lastRun))
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || Number(b.financial_impact) - Number(a.financial_impact))
  const cleared = mine.filter((f) => f.status !== 'open' && f.status !== 'resolved' && !stillDetected(f, lastRun))

  const missingFinding = counted.some((f) => f.rule_code === 'missing_required')
  const active = project.status.toLowerCase() === 'active'
  const blankNow = active && isBlank(project, currentMonth)
  const others = counted.filter((f) => f.rule_code !== 'missing_required')

  let status: ProjectStatus = 'on_track'
  if (others.length > 0) status = 'off_track'
  else if (missingFinding || blankNow) status = 'missing_entry'

  const hasError = counted.some((f) => f.severity === 'error')
  const priority: Priority = hasError || status === 'missing_entry' ? 'High' : status === 'off_track' ? 'Medium' : 'Low'

  const top = counted[0]
  const awaitingReview = counted.some((f) => f.awaiting_review)
  let action = 'No action needed'
  if (awaitingReview) action = 'Review the investigation'
  else if (top) action = ACTION_BY_RULE[top.rule_code] ?? 'Review the flagged item'
  else if (blankNow) action = `Enter the forecast for ${currentMonth.slice(0, 7)}`

  return {
    project,
    status,
    priority,
    findings: counted,
    clearedFindings: cleared,
    action,
    owner: top?.routed_to_name ?? project.client_partner?.name ?? project.commercial_owner?.name ?? 'Unassigned',
    impact: counted.filter((f) => f.project_id === project.id).reduce((sum, f) => sum + Number(f.financial_impact), 0),
    awaitingReview,
  }
}

const STATUS_ORDER: Record<ProjectStatus, number> = { off_track: 0, missing_entry: 1, on_track: 2 }

/** Urgent first: status, then the most severe finding, then the most revenue at stake. */
export function byPriority(a: ProjectRow, b: ProjectRow): number {
  const rank = (r: ProjectRow) => SEVERITY_RANK[r.findings[0]?.severity ?? 'info']
  return (
    STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
    rank(a) - rank(b) ||
    b.impact - a.impact ||
    a.project.customer.localeCompare(b.project.customer)
  )
}
