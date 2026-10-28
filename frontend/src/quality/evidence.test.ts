import { describe, expect, it } from 'vitest'
import { describeStep, evidenceRows } from './evidence'

describe('evidence as business records', () => {
  it('never shows JSON: nested records become readable text', () => {
    const rows = evidenceRows({
      project: { row_id: 'R1008', customer: 'Data Platform Co' },
      months: [
        { month: '2027-01-01', amount: '30000.00' },
        { month: '2027-02-01', amount: '51000.00' },
      ],
    })
    const text = rows.map((r) => `${r.label} ${r.value}`).join(' ')
    expect(text).not.toMatch(/[{}[\]"]/)
    expect(rows.find((r) => r.label === 'Months')?.value).toBe('Jan 2027: $30,000; Feb 2027: $51,000')
    expect(rows.find((r) => r.label === 'Project')?.value).toBe('Row id R1008, Customer Data Platform Co')
  })

  it('formats amounts, percentages and dates, and says Yes or No for booleans', () => {
    const rows = Object.fromEntries(
      evidenceRows({ before: '43000', after: '75000', change_percent: '74.4', as_of: '2026-10-01', in_forecast: true, note: '' }).map((r) => [r.label, r.value]),
    )
    expect(rows['Before']).toBe('$43,000')
    expect(rows['After']).toBe('$75,000')
    expect(rows['Change percent']).toBe('74.4%')
    expect(rows['As of']).toBe('Oct 2026')
    expect(rows['In forecast']).toBe('Yes')
    expect(rows['Note']).toBe('—')
  })

  it('hides internal database identifiers but keeps business references', () => {
    const labels = evidenceRows({ id: 4, project_id: 1, hubspot_deal_id: '65394918517', customer: 'Acme' }).map((r) => r.label)
    expect(labels).toEqual(['Hubspot deal id', 'Customer'])
  })

  it('caps long lists instead of dumping them', () => {
    const value = evidenceRows({ items: Array.from({ length: 9 }, (_, i) => `row ${i}`) })[0].value
    expect(value).toContain('and 3 more')
  })

  it('describes the investigator’s steps in plain words', () => {
    expect(describeStep('get_project_history')).toBe('Read the project’s change history')
    expect(describeStep('some_new_tool')).toBe('Some new tool')
  })
})
