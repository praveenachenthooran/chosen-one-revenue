import { describe, expect, it } from 'vitest'
import type { AuthUser } from './api'
import { EXPERIENCE_LABEL, experiencesFor } from './roles'

const user = (...roles: string[]): AuthUser => ({ id: 1, name: 'Test', email: null, roles, can_edit_all: false })

describe('role landing pages (presentation only)', () => {
  it('sends each of the three demo roles to its own experience', () => {
    expect(experiencesFor(user('Client Partner'))).toEqual(['partner'])
    expect(experiencesFor(user('Commercial Owner'))).toEqual(['portfolio'])
    expect(experiencesFor(user('CRO / Sales Leadership'))).toEqual(['leadership'])
  })

  it('gives Administrators the overview and the portfolio', () => {
    expect(experiencesFor(user('Administrator'))).toEqual(['leadership', 'portfolio'])
  })

  it('gives everyone else the read-only portfolio', () => {
    for (const role of ['Finance', 'Seller / Account Executive', 'Delivery / Resourcing']) {
      expect(experiencesFor(user(role))).toEqual(['portfolio'])
    }
    expect(experiencesFor(user())).toEqual(['portfolio'])
  })

  it('names the portfolio by what the person is', () => {
    expect(EXPERIENCE_LABEL.portfolio(user('Commercial Owner'))).toBe('Portfolio')
    expect(EXPERIENCE_LABEL.portfolio(user('Finance'))).toBe('All projects')
  })
})
