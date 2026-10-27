import { useState } from 'react'
import type { ImportRun } from '../api'
import { formatMonthLabel } from '../format'

export default function ImportStatus({ run }: { run: ImportRun }) {
  const [open, setOpen] = useState(false)
  const variances = run.reconciliation.filter((r) => !r.matches)
  const notes = run.issues.length

  return (
    <section className="import-status" aria-label="Excel import status">
      <div className="d-flex flex-wrap align-items-center gap-3">
        <span className={`recon-badge ${run.reconciled ? 'recon-ok' : 'recon-bad'}`}>
          {run.reconciled ? 'Reconciled to source' : `${variances.length} month(s) off source`}
        </span>
        <span className="text-secondary small">
          {run.source_file}
          {run.source_as_of ? ` · as of ${run.source_as_of}` : ''} · {run.rows_read} rows read ·{' '}
          {run.rows_rejected} rejected
        </span>
        {notes > 0 && (
          <button
            type="button"
            className="btn btn-link btn-sm p-0"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {notes} import note{notes === 1 ? '' : 's'}
          </button>
        )}
      </div>
      {open && (
        <ul className="import-issues small">
          {run.issues.map((issue, index) => (
            <li key={index}>
              <strong>{issue.source_row_id ?? `row ${issue.row_number}`}</strong>{' '}
              {issue.row_rejected ? '(rejected) ' : ''}
              {issue.message}
            </li>
          ))}
          {variances.map((v) => (
            <li key={v.month}>
              {formatMonthLabel(v.month)} differs from the workbook by {v.variance}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
