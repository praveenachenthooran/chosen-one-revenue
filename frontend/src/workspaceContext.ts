import { createContext, useContext } from 'react'
import type { Finding, Health, MonthColumn, Project } from './api'
import type { SaveAmount } from './editing'
import type { ProjectRow } from './projects/status'

export type Data = { projects: Project[]; months: MonthColumn[]; asOf: string | null; findings: Finding[]; health: Health | null }
export type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; data: Data }
export type Notice = { kind: 'success' | 'error'; text: string }

export type Workspace = {
  state: State
  /** The current month as `YYYY-MM-01`, from the data's as-of date. */
  currentMonth: string
  rows: ProjectRow[]
  saveAmount: SaveAmount
  pending: ReadonlySet<string>
  /** Bumps after every saved edit so detail panels and charts refetch. */
  revision: number
  notice: Notice | null
  setNotice: (notice: Notice | null) => void
  reload: () => void
  reloadFindings: () => Promise<void>
}

export const WorkspaceContext = createContext<Workspace | null>(null)

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error('useWorkspace must be used inside WorkspaceProvider')
  return value
}

export function currentMonthOf(asOf: string | null): string {
  const source = asOf ?? new Date().toISOString()
  return `${source.slice(0, 7)}-01`
}
