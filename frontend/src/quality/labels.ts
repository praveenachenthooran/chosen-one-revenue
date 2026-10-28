export const RULE_LABELS: Record<string, string> = {
  duplicate_project: 'Possible duplicate project',
  closed_won_missing: 'Closed Won not in committed forecast',
  amount_mismatch: 'HubSpot amount vs schedule',
  stale_row: 'Not updated recently',
  mom_anomaly: 'Unusual month-over-month movement',
  missing_required: 'Missing required fields',
  deal_missing_fields: 'HubSpot deal cannot be phased',
}

export const STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  accepted: 'Accepted',
  dismissed: 'Dismissed',
  resolved: 'Resolved',
}

export const CAUSE_LABELS = {
  confirmed: 'Confirmed',
  hypothesis: 'Hypothesis',
  insufficient_evidence: 'Insufficient evidence',
} as const
