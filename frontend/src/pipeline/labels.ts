import type { DealStatus } from '../api'

export const STATUS_LABELS: Record<DealStatus, string> = {
  weighted: 'Weighted pipeline',
  awaiting_confirmation: 'Awaiting confirmation',
  closed_won_pending: 'Closed Won, no project yet',
  linked: 'In forecast (committed)',
  closed_lost: 'Closed Lost',
  excluded: 'Not in forecast',
}

export const STATUS_HELP: Record<DealStatus, string> = {
  weighted: 'Counts at stage probability, spread from the expected start.',
  awaiting_confirmation:
    'Won. A draft project was created; it counts at 100% as pipeline until its owner confirms.',
  closed_won_pending: 'Won, but no draft project could be created. See the findings.',
  linked: 'Represented by a committed project, so it has no weighted line.',
  closed_lost: 'Lost: removed from the forecast.',
  excluded: 'Below the forecast stage, or no longer returned by HubSpot.',
}

export const EVENT_LABELS: Record<string, string> = {
  deal_entered_forecast: 'Entered the forecast',
  deal_removed_from_forecast: 'Removed from the forecast',
  deal_closed_won: 'Closed Won',
  deal_closed_lost: 'Closed Lost',
  deal_linked: 'Linked to a project',
  stage_changed: 'Stage changed',
  draft_created: 'Draft project created',
  draft_confirmed: 'Draft confirmed',
  weighted_line_retired: 'Weighted line retired',
  draft_overdue: 'Confirmation overdue',
  stage_updated: 'Stage probability changed',
}
