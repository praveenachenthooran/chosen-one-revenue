import type { AuthUser } from './api'

/** What each person lands on. This is presentation only: the server still decides who may edit or see what. */
export type Experience = 'partner' | 'portfolio' | 'leadership'

const SALES_LEADERSHIP = 'CRO / Sales Leadership'
const COMMERCIAL_OWNER = 'Commercial Owner'
const CLIENT_PARTNER = 'Client Partner'
const ADMIN = 'Administrator'

export const ACTUALS_VIEWERS = ['Finance', SALES_LEADERSHIP, ADMIN]
export const ACTUALS_LOADERS = ['Finance', ADMIN]

export const hasRole = (user: AuthUser, ...roles: string[]) => user.roles.some((r) => roles.includes(r))

/** The experiences a person can open, most relevant first. Everyone else gets the read-only portfolio. */
export function experiencesFor(user: AuthUser): Experience[] {
  const list: Experience[] = []
  if (hasRole(user, SALES_LEADERSHIP, ADMIN)) list.push('leadership')
  if (hasRole(user, COMMERCIAL_OWNER, ADMIN)) list.push('portfolio')
  if (hasRole(user, CLIENT_PARTNER)) list.push('partner')
  if (list.length === 0) list.push('portfolio') // Finance, Seller / AE, Resourcing: the read-only portfolio
  return list
}

export const EXPERIENCE_LABEL: Record<Experience, (user: AuthUser) => string> = {
  leadership: () => 'Revenue overview',
  partner: () => 'My projects',
  portfolio: (user) => (hasRole(user, COMMERCIAL_OWNER) ? 'Portfolio' : 'All projects'),
}
