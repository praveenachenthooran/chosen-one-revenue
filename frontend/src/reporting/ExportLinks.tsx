import { useState } from 'react'
import { exportUrl, type CsvView, type ForecastFilters } from '../api'

const CSV_VIEWS: [CsvView, string][] = [
  ['monthly', 'Monthly totals'],
  ['projects', 'Committed by project'],
  ['pipeline', 'Pipeline by deal'],
]

type Props = {
  /** A saved snapshot to export, or null for the current forecast. */
  snapshotId: number | null
  filters: ForecastFilters
}

/** Download links for the current view or a snapshot. The files carry the export time and the
 *  filters applied, and their totals are the ones shown on screen. */
export default function ExportLinks({ snapshotId, filters }: Props) {
  const [csvView, setCsvView] = useState<CsvView>('monthly')
  const subject = snapshotId === null ? 'current forecast' : 'snapshot'
  return (
    <div className="export-links" role="group" aria-label={`Export the ${subject}`}>
      <span className="text-secondary small">Export {subject}:</span>
      <a className="btn btn-outline-secondary btn-sm" href={exportUrl({ snapshotId }, filters, 'xlsx')} download>
        Excel (.xlsx)
      </a>
      <a className="btn btn-outline-secondary btn-sm" href={exportUrl({ snapshotId }, filters, 'csv', csvView)} download>
        CSV
      </a>
      <select
        className="form-select form-select-sm"
        aria-label="CSV contents"
        value={csvView}
        onChange={(event) => setCsvView(event.target.value as CsvView)}
      >
        {CSV_VIEWS.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <span className="text-secondary small">Includes the export time and the filters applied.</span>
    </div>
  )
}
