import { useMemo, useState } from 'react'
import type { AuthUser } from '../api'
import { formatMoney } from '../format'
import { ErrorState, KpiStrip, Loading, PageHeader } from '../ui/Panel'
import { useWorkspace } from '../workspaceContext'
import FilterBar from './FilterBar'
import { applyFilters, NO_FILTERS, type ProjectFilters } from './filters'
import { STATUS_LABEL, STATUS_TONE } from './status'

const PRIORITY_TONE = { High: 'red', Medium: 'amber', Low: '' } as const

/** The Client Partner's landing page: what needs my attention, most urgent first. No graph here. */
export default function MyProjects({ user, onOpen }: { user: AuthUser; onOpen: (projectId: number) => void }) {
  const ws = useWorkspace()
  const [filters, setFilters] = useState<ProjectFilters>(NO_FILTERS)

  const mine = useMemo(() => ws.rows.filter((r) => user.can_edit_all || r.project.can_edit), [ws.rows, user.can_edit_all])
  const shown = useMemo(() => applyFilters(mine, filters), [mine, filters])

  if (ws.state.kind === 'loading') return <Loading text="Loading your projects…" />
  if (ws.state.kind === 'error') return <ErrorState text="Your projects could not be loaded." onRetry={ws.reload} />

  const count = (s: string) => mine.filter((r) => r.status === s).length
  const atStake = mine.reduce((sum, r) => sum + r.impact, 0)

  return (
    <>
      <PageHeader title="My projects" sub="Projects you own. The ones that need attention come first." />
      <div className="stack">
        <KpiStrip
          items={[
            { label: 'Projects', value: String(mine.length) },
            { label: 'Off Track', value: String(count('off_track')), note: count('off_track') ? 'Flagged by the checks' : 'None flagged', tone: count('off_track') ? 'bad' : 'good' },
            { label: 'Missing Entry', value: String(count('missing_entry')), note: count('missing_entry') ? 'Forecast needed' : 'None missing', tone: count('missing_entry') ? 'bad' : 'good' },
            { label: 'Revenue at stake', value: formatMoney(atStake), note: 'On flagged projects' },
          ]}
        />
        <FilterBar
          fields={['status', 'customer', 'owner', 'practice', 'month']}
          rows={mine}
          months={ws.state.data.months}
          value={filters}
          onChange={setFilters}
          shown={shown.length}
          searchLabel="Customer or project"
        />
        <section className="panel panel-flush">
          {mine.length === 0 ? (
            <div className="empty">
              <b>No projects are assigned to you yet</b>An administrator can assign you as a project owner.
            </div>
          ) : shown.length === 0 ? (
            <div className="empty">
              <b>No projects match these filters</b>
              <button type="button" className="linkbtn" onClick={() => setFilters(NO_FILTERS)}>
                Clear filters
              </button>
            </div>
          ) : (
            <div className="tscroll capped">
              <table className="tbl" style={{ minWidth: 860 }}>
                <thead>
                  <tr>
                    <th style={{ paddingLeft: 22 }}>Project</th>
                    <th>Status</th>
                    <th>Owner</th>
                    <th className="r">Revenue at stake</th>
                    <th>Required action</th>
                    <th style={{ paddingRight: 22 }}>Priority</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr
                      key={r.project.id}
                      data-open
                      tabIndex={0}
                      aria-label={`Open ${r.project.customer}`}
                      onClick={() => onOpen(r.project.id)}
                      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onOpen(r.project.id))}
                    >
                      <td className="pname" style={{ paddingLeft: 22 }}>
                        <b>{r.project.customer}</b>
                        <span>{r.project.name}</span>
                      </td>
                      <td>
                        <span className={`rag ${STATUS_TONE[r.status]}`}>{STATUS_LABEL[r.status]}</span>
                      </td>
                      <td>{r.owner}</td>
                      <td className="r">{r.impact > 0 ? formatMoney(r.impact) : '—'}</td>
                      <td>{r.action}</td>
                      <td style={{ paddingRight: 22 }}>
                        <span className={`pill ${PRIORITY_TONE[r.priority]}`}>{r.priority}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  )
}
