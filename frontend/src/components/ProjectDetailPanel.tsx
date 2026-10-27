import { useEffect, useState } from 'react'
import { fetchProject, type Project, type ProjectDetail } from '../api'
import { formatMoney, formatMonthLabel } from '../format'

type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; detail: ProjectDetail }

export default function ProjectDetailPanel({
  project,
  onClose,
}: {
  project: Project
  onClose: () => void
}) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetchProject(project.id, controller.signal)
      .then((detail) => setState({ kind: 'ready', detail }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState({ kind: 'error' })
      })
    return () => controller.abort()
  }, [project.id])

  return (
    <aside className="detail-panel" aria-label={`Details for ${project.name}`}>
      <div className="d-flex justify-content-between align-items-start mb-3">
        <div>
          <p className="eyebrow mb-1">{project.customer}</p>
          <h2 className="h5 mb-0">{project.name}</h2>
        </div>
        <button type="button" className="btn-close" aria-label="Close details" onClick={onClose} />
      </div>
      {state.kind === 'loading' && <p className="text-secondary">Loading…</p>}
      {state.kind === 'error' && <p className="text-danger">Could not load this project.</p>}
      {state.kind === 'ready' && (
        <>
          {state.detail.attention.length > 0 && (
            <div className="alert alert-warning py-2 small" role="note">
              <strong>Needs attention</strong>
              <ul className="mb-0 ps-3">
                {state.detail.attention.map((a) => (
                  <li key={a.code}>{a.message}</li>
                ))}
              </ul>
            </div>
          )}
          <dl className="detail-list">
            <dt>Status</dt>
            <dd>{state.detail.status}</dd>
            {state.detail.owners.flatMap((o) => [
              <dt key={`${o.role}-t`}>{o.role.replace('_', ' ')}</dt>,
              <dd key={`${o.role}-d`}>{o.name}</dd>,
            ])}
            <dt>Total</dt>
            <dd>{formatMoney(state.detail.total_revenue)}</dd>
            <dt>Source</dt>
            <dd>
              {state.detail.source_row_id}
              {state.detail.source_row_number ? ` (row ${state.detail.source_row_number})` : ''}
            </dd>
          </dl>
          <table className="table table-sm">
            <thead>
              <tr>
                <th>Month</th>
                <th>FY</th>
                <th className="text-end">Revenue</th>
              </tr>
            </thead>
            <tbody>
              {state.detail.monthly_revenue.map((m) => (
                <tr key={m.month}>
                  <td>{formatMonthLabel(m.month)}</td>
                  <td>{m.fiscal_year}</td>
                  <td className="text-end">{formatMoney(m.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-secondary small mb-0">Read-only until sign-in and permissions arrive.</p>
        </>
      )}
    </aside>
  )
}
