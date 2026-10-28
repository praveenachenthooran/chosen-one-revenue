import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  fetchActualMonths,
  fetchUploads,
  uploadActuals,
  type ActualMonth,
  type ReconciliationReport,
  type UploadSummary,
} from '../api'
import { formatMoneyExact, formatMonthLabel } from '../format'

const when = (iso: string) => new Date(iso).toLocaleString()

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

type Outcome =
  | { kind: 'report'; ok: boolean; dryRun: boolean; report: ReconciliationReport; uploadId: number | null; text: string }
  | { kind: 'error'; text: string }

function Reconciliation({ outcome }: { outcome: Extract<Outcome, { kind: 'report' }> }) {
  const { report } = outcome
  return (
    <section className="report-section" aria-label="Reconciliation report">
      <div className={`notice notice-${outcome.ok ? 'success' : 'error'} mb-2`} role={outcome.ok ? 'status' : 'alert'}>
        <span>{outcome.text}</span>
      </div>
      <p className="small mb-2">
        <strong>{report.filename}</strong>: {report.rows_total} rows read, {report.rows_rejected} with problems
        {report.rows_new !== undefined && (
          <>
            {' '}
            · {report.rows_new} new, {report.rows_replaced} replacing earlier values, {report.rows_unchanged} unchanged
          </>
        )}
        {' '}
        · {report.project_level_rows} by project, {report.customer_level_rows} by customer · total {formatMoneyExact(report.total_amount)}
      </p>
      {report.ignored_columns.length > 0 && (
        <p className="small text-secondary">Ignored columns: {report.ignored_columns.join(', ')}.</p>
      )}
      {report.months.length > 0 && (
        <div className="table-responsive">
          <table className="table table-sm" aria-label="Reconciliation by month">
            <thead>
              <tr>
                <th scope="col">Month</th>
                <th scope="col" className="text-end">
                  Rows
                </th>
                <th scope="col" className="text-end">
                  Actual in file
                </th>
                <th scope="col" className="text-end">
                  Committed forecast now
                </th>
              </tr>
            </thead>
            <tbody>
              {report.months.map((m) => (
                <tr key={m.month}>
                  <th scope="row">{formatMonthLabel(m.month)}</th>
                  <td className="text-end">{m.rows}</td>
                  <td className="text-end">{formatMoneyExact(m.actual_total)}</td>
                  <td className="text-end">{formatMoneyExact(m.committed_forecast_now)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.errors.length > 0 && (
        <>
          <h3 className="h6">
            Problems to fix{report.errors_total > report.errors.length ? ` (first ${report.errors.length} of ${report.errors_total})` : ''}
          </h3>
          <div className="table-responsive">
            <table className="table table-sm" aria-label="Upload problems">
              <thead>
                <tr>
                  <th scope="col">Row</th>
                  <th scope="col">Column</th>
                  <th scope="col">Problem</th>
                </tr>
              </thead>
              <tbody>
                {report.errors.map((e, index) => (
                  <tr key={`${e.row}-${e.code}-${index}`}>
                    <td>{e.row}</td>
                    <td>{e.field ?? '—'}</td>
                    <td>{e.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {report.warnings.length > 0 && (
        <p className="small text-secondary mb-0">
          {report.warnings.length} amount(s) had more than two decimals and were rounded to cents (rows{' '}
          {report.warnings.map((w) => w.row).join(', ')}).
        </p>
      )}
    </section>
  )
}

type Props = { canUpload: boolean; onSessionEnded: () => void }

/** Monthly actuals: upload a CSV or XLSX, see the reconciliation, and review earlier uploads. */
export default function ActualsView({ canUpload, onSessionEnded }: Props) {
  const [uploads, setUploads] = useState<UploadSummary[] | null>(null)
  const [months, setMonths] = useState<ActualMonth[]>([])
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const sessionEnded = useCallback(
    (error: unknown) => {
      if (isAbort(error)) return true
      if (error instanceof ApiError && error.status === 401) {
        onSessionEnded()
        return true
      }
      return false
    },
    [onSessionEnded],
  )

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([fetchUploads(controller.signal), fetchActualMonths(controller.signal)])
      .then(([u, m]) => {
        setUploads(u)
        setMonths(m)
        setFailed(false)
      })
      .catch((error: unknown) => {
        if (!sessionEnded(error)) setFailed(true)
      })
    return () => controller.abort()
  }, [attempt, sessionEnded])

  async function submit(dryRun: boolean) {
    if (!file) return
    setBusy(true)
    setOutcome(null)
    try {
      const result = await uploadActuals(file, dryRun)
      setOutcome({
        kind: 'report',
        ok: result.status === 'completed',
        dryRun,
        report: result.report,
        uploadId: result.upload_id,
        text: dryRun
          ? 'File checked: it is valid. Nothing has been saved yet.'
          : `Actuals loaded (upload #${result.upload_id}).`,
      })
      if (!dryRun) {
        setFile(null)
        if (input.current) input.current.value = ''
        setAttempt((n) => n + 1)
      }
    } catch (error) {
      if (sessionEnded(error)) return
      if (error instanceof ApiError && error.detail && typeof error.detail.report === 'object' && error.detail.report) {
        setOutcome({
          kind: 'report',
          ok: false,
          dryRun,
          report: error.detail.report as ReconciliationReport,
          uploadId: (error.detail.upload_id as number | null) ?? null,
          text: error.message,
        })
        setAttempt((n) => n + 1) // the rejected attempt appears in the history
      } else {
        setOutcome({ kind: 'error', text: error instanceof ApiError ? error.message : 'The file could not be sent.' })
      }
    } finally {
      setBusy(false)
    }
  }

  if (failed) {
    return (
      <div className="state-box" role="alert">
        <p className="mb-2">Actuals could not be loaded.</p>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </div>
    )
  }

  return (
    <div className="report-layout">
      <section className="report-section" aria-label="Load actuals">
        <h2 className="h6">Monthly actuals</h2>
        <p className="small text-secondary mb-2">
          One row per customer or project and month, from a .csv or .xlsx file with a header row. Month and Amount are required, plus
          a Project ID (the Row ID, e.g. R1001) or a Customer, with Project to narrow it to one project. Amounts are in the same
          currency as the forecast. A file is loaded all or nothing: if any row has a problem, none are saved and every problem is
          listed. Loading a month again replaces the earlier values for the same customer or project; the earlier values are kept on
          record.
        </p>
        {canUpload ? (
          <form
            className="d-flex flex-wrap gap-2 align-items-center"
            onSubmit={(event) => {
              event.preventDefault()
              void submit(false)
            }}
          >
            <input
              ref={input}
              type="file"
              className="form-control form-control-sm actuals-file"
              aria-label="Actuals file"
              accept=".csv,.xlsx"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null)
                setOutcome(null)
              }}
            />
            <button type="button" className="btn btn-outline-secondary btn-sm" disabled={!file || busy} onClick={() => void submit(true)}>
              Check file
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={!file || busy}>
              {busy ? 'Working…' : 'Upload actuals'}
            </button>
          </form>
        ) : (
          <p className="small text-secondary mb-0">Finance and Administrators load actuals; you can review what has been loaded.</p>
        )}
      </section>

      {outcome?.kind === 'error' && (
        <div className="notice notice-error" role="alert">
          <span>{outcome.text}</span>
        </div>
      )}
      {outcome?.kind === 'report' && <Reconciliation outcome={outcome} />}

      {months.length > 0 && (
        <section className="report-section" aria-label="Actuals on record">
          <h3 className="h6">Actuals on record</h3>
          <div className="table-responsive">
            <table className="table table-sm mb-0" aria-label="Actuals by month">
              <thead>
                <tr>
                  <th scope="col">Month</th>
                  <th scope="col" className="text-end">
                    Actual
                  </th>
                  <th scope="col" className="text-end">
                    Committed forecast now
                  </th>
                </tr>
              </thead>
              <tbody>
                {months.map((m) => (
                  <tr key={m.month}>
                    <th scope="row">{formatMonthLabel(m.month)}</th>
                    <td className="text-end">{formatMoneyExact(m.actual_total)}</td>
                    <td className="text-end">{formatMoneyExact(m.committed_forecast)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="report-section" aria-label="Upload history">
        <h3 className="h6">Upload history</h3>
        {uploads === null ? (
          <p className="text-secondary small mb-0" role="status">
            Loading…
          </p>
        ) : uploads.length === 0 ? (
          <p className="text-secondary small mb-0">No actuals have been uploaded yet.</p>
        ) : (
          <div className="table-responsive">
            <table className="table table-sm mb-0" aria-label="Uploads">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">File</th>
                  <th scope="col">When</th>
                  <th scope="col">By</th>
                  <th scope="col">Result</th>
                  <th scope="col" className="text-end">
                    Rows
                  </th>
                  <th scope="col" className="text-end">
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {uploads.map((u) => (
                  <tr key={u.id}>
                    <td>{u.id}</td>
                    <td>{u.filename}</td>
                    <td>{when(u.uploaded_at)}</td>
                    <td>{u.uploaded_by_label}</td>
                    <td>
                      <span className={`upload-status upload-${u.status}`}>
                        {u.status === 'completed' ? 'Loaded' : `Rejected (${u.rows_rejected} bad rows)`}
                      </span>
                    </td>
                    <td className="text-end">{u.rows_total}</td>
                    <td className="text-end">{u.status === 'completed' ? formatMoneyExact(u.total_amount) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
