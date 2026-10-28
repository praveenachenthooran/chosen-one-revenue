import { describe, expect, it } from 'vitest'
import type { Finding, Project } from '../api'
import { finding1, finding2, projects } from '../test/fixtures'
import { buildRow, byPriority, stillDetected } from './status'

const NOW = '2026-10-01'
const RUN_AT = '2026-10-27T02:00:00Z'
const health = { last_success: { started_at: RUN_AT } } as never

const project: Project = { ...projects[0], status: 'Active', monthly: { '2026-10-01': '1000.00' } }
const finding = (over: Partial<Finding>): Finding => ({ ...finding1, project_id: project.id, last_seen_at: RUN_AT, ...over })

describe('project status (deterministic, from findings and the forecast)', () => {
  it('is On Track with nothing flagged and a forecast entered', () => {
    const row = buildRow(project, [], health, NOW)
    expect(row.status).toBe('on_track')
    expect(row.priority).toBe('Low')
    expect(row.action).toBe('No action needed')
  })

  it('is Off Track with a still-open warning, priority Medium, and names the action', () => {
    const row = buildRow(project, [finding({ rule_code: 'mom_anomaly', severity: 'warning' })], health, NOW)
    expect(row.status).toBe('off_track')
    expect(row.priority).toBe('Medium')
    expect(row.action).toBe('Verify the change in monthly revenue')
    expect(row.impact).toBe(21000)
  })

  it('treats an error as High priority', () => {
    const row = buildRow(project, [finding({ severity: 'error', rule_code: 'duplicate_project' })], health, NOW)
    expect(row.status).toBe('off_track')
    expect(row.priority).toBe('High')
  })

  it('is Missing Entry for a missing-required finding, and for a blank current month on an active project', () => {
    expect(buildRow(project, [finding({ rule_code: 'missing_required', severity: 'error' })], health, NOW).status).toBe('missing_entry')
    const blank = { ...project, monthly: { '2026-11-01': '500.00' } }
    const row = buildRow(blank, [], health, NOW)
    expect(row.status).toBe('missing_entry')
    expect(row.priority).toBe('High')
  })

  it('does not treat an explicit zero as a missing entry', () => {
    expect(buildRow({ ...project, monthly: { '2026-10-01': '0.00' } }, [], health, NOW).status).toBe('on_track')
  })

  it('ignores a blank month on a completed project', () => {
    expect(buildRow({ ...project, status: 'Completed', monthly: {} }, [], health, NOW).status).toBe('on_track')
  })

  it('does not turn green when a finding is accepted or dismissed while the condition is still detected', () => {
    for (const status of ['accepted', 'dismissed'] as const) {
      const row = buildRow(project, [finding({ status, last_seen_at: RUN_AT })], health, NOW)
      expect(row.status).toBe('off_track')
    }
  })

  it('turns On Track only once the checks no longer detect the condition', () => {
    const cleared = finding({ status: 'accepted', last_seen_at: '2026-10-20T02:00:00Z' }) // older than the last run
    const row = buildRow(project, [cleared], health, NOW)
    expect(row.status).toBe('on_track')
    expect(row.clearedFindings).toHaveLength(1)
    expect(buildRow(project, [finding({ status: 'resolved' })], health, NOW).status).toBe('on_track')
  })

  it('keeps a reviewed finding when it cannot prove the condition cleared', () => {
    expect(stillDetected(finding({ status: 'dismissed', last_seen_at: null }), RUN_AT)).toBe(true)
    expect(stillDetected(finding({ status: 'dismissed' }), null)).toBe(true)
  })

  it('ignores informational findings and findings about other projects', () => {
    const rows = buildRow(project, [finding({ severity: 'info' }), finding({ project_id: 999, id: 99 })], health, NOW)
    expect(rows.status).toBe('on_track')
  })

  it('counts a related project as affected but attributes the revenue to the primary one only', () => {
    const duplicate = finding({ project_id: 999, related_project_ids: [project.id], rule_code: 'duplicate_project', severity: 'error' })
    const row = buildRow(project, [duplicate], health, NOW)
    expect(row.status).toBe('off_track')
    expect(row.impact).toBe(0)
  })

  it('routes the action to the finding owner, and asks for a review when an investigation is waiting', () => {
    const row = buildRow(project, [finding({ awaiting_review: true, routed_to_name: 'Riley Shah' })], health, NOW)
    expect(row.owner).toBe('Riley Shah')
    expect(row.action).toBe('Review the investigation')
  })

  it('orders the urgent projects first: Off Track, then Missing Entry, then On Track; larger impact first', () => {
    const a = buildRow({ ...project, id: 1, customer: 'A' }, [], health, NOW)
    const b = buildRow({ ...project, id: 2, customer: 'B' }, [finding({ project_id: 2, id: 2, financial_impact: '5000.00' })], health, NOW)
    const c = buildRow({ ...project, id: 3, customer: 'C' }, [finding({ project_id: 3, id: 3, financial_impact: '90000.00' })], health, NOW)
    const d = buildRow({ ...project, id: 4, customer: 'D', monthly: {} }, [], health, NOW)
    expect([a, b, c, d].sort(byPriority).map((r) => r.project.customer)).toEqual(['C', 'B', 'D', 'A'])
    expect(finding2.severity).toBe('error')
  })
})
