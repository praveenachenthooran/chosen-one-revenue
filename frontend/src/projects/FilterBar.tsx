import type { MonthColumn } from '../api'
import { NO_FILTERS, ownerNamesOf, type FilterField, type ProjectFilters } from './filters'
import { STATUS_LABEL, type ProjectRow, type ProjectStatus } from './status'

const ownerNames = ownerNamesOf

const distinct = (values: (string | null | undefined)[]) => [...new Set(values.filter((v): v is string => Boolean(v)))].sort((a, b) => a.localeCompare(b))

function Select({ id, label, value, onChange, options }: { id: string; label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="form-select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </div>
  )
}

type Props = {
  fields: FilterField[]
  rows: ProjectRow[]
  months: MonthColumn[]
  value: ProjectFilters
  onChange: (next: ProjectFilters) => void
  shown: number
  searchLabel: string
}

/** One filter bar for every project list, so the controls sit and read the same on every screen. */
export default function FilterBar({ fields, rows, months, value, onChange, shown, searchLabel }: Props) {
  const set = (key: keyof ProjectFilters) => (v: string) => onChange({ ...value, [key]: v })
  const active = Object.values(value).some(Boolean)
  const options: Record<FilterField, [string, string][]> = {
    status: (Object.keys(STATUS_LABEL) as ProjectStatus[]).map((s) => [s, STATUS_LABEL[s]]),
    customer: distinct(rows.map((r) => r.project.customer)).map((v) => [v, v]),
    owner: distinct(rows.flatMap(ownerNames)).map((v) => [v, v]),
    practice: distinct(rows.map((r) => r.project.practice)).map((v) => [v, v]),
    region: distinct(rows.map((r) => r.project.region)).map((v) => [v, v]),
    month: months.map((m) => [m.month, m.label]),
  }
  const labels: Record<FilterField, string> = { status: 'Status', customer: 'Customer', owner: 'Owner', practice: 'Practice', region: 'Region', month: 'Month' }
  return (
    <section className="panel" aria-label="Filters">
      <div className="filters" style={{ ['--filter-cols' as string]: fields.length }}>
        <div className="field">
          <label htmlFor="f-q">Search</label>
          <input id="f-q" className="input" type="search" value={value.q} placeholder={searchLabel} onChange={(e) => set('q')(e.target.value)} />
        </div>
        {fields.map((f) => (
          <Select key={f} id={`f-${f}`} label={labels[f]} value={value[f]} onChange={set(f)} options={options[f]} />
        ))}
      </div>
      <div className="fsummary">
        <span>
          Showing {shown} of {rows.length} project{rows.length === 1 ? '' : 's'}
          {active ? ' matching your filters' : ''}.
        </span>
        {active && (
          <button type="button" className="linkbtn" onClick={() => onChange(NO_FILTERS)}>
            Clear filters
          </button>
        )}
      </div>
    </section>
  )
}
