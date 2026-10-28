import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  fetchFinding,
  fetchHistory,
  fetchProject,
  runChecks,
  type FindingDetail,
  type HistoryItem,
  type ProjectDetail,
} from '../api'
import { formatDate, formatMoney, formatMoneyExact, formatMonthLabel, tidyMoney } from '../format'
import DetectiveDrawer from '../quality/DetectiveDrawer'
import { RULE_LABELS } from '../quality/labels'
import { ErrorState, Fold, Loading, Notice, PageHeader } from '../ui/Panel'
import { icons } from '../ui/icons'
import { useWorkspace } from '../workspaceContext'
import MonthlyForecast from './MonthlyForecast'
import ProjectChart from './ProjectChart'
import { STATUS_LABEL, STATUS_TONE } from './status'

type Props = {
  projectId: number
  backLabel: string
  onBack: () => void
  /** Collapsed for Client Partners and Commercial Owners (Michael's feedback); open from the leadership overview. */
  chartOpen: boolean
  onSessionEnded: () => void
  onOpenDeal: (deal: { id: number; title: string }) => void
}

type History = { kind: 'hidden' } | { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; items: HistoryItem[] }
const SEVERITY_TONE = { error: 'red', warning: 'amber', info: '' } as const

function describe(item: HistoryItem): string {
  if (item.field === 'revenue' && item.month) return `${formatMonthLabel(item.month)} forecast`
  if (item.field === 'baseline' && item.month) return `${formatMonthLabel(item.month)} (original import)`
  if (item.field === 'project' && item.change_type === 'create') return 'Imported from the Excel workbook'
  return item.field.replace('owner:', 'Owner: ').replace(/_/g, ' ')
}

function HistoryList({ history }: { history: History }) {
  if (history.kind === 'hidden')
    return <p className="hint">Change history is visible to this project’s owners, Finance, Sales leadership and Administrators.</p>
  if (history.kind === 'loading') return <p className="hint">Loading history…</p>
  if (history.kind === 'error') return <p className="text-danger">Could not load the history.</p>
  if (history.items.length === 0) return <p className="hint">No changes recorded yet.</p>
  return (
    <ol className="timeline">
      {history.items.map((item) => (
        <li key={item.id}>
          <time dateTime={item.created_at}>
            {new Date(item.created_at).toLocaleString()} · {item.from_import ? 'Excel import' : item.actor_name}
          </time>
          <div>
            <strong>{describe(item)}</strong>
            {item.field === 'revenue' || item.field === 'baseline' ? (
              <>
                : {formatMoneyExact(item.old_value)} → {formatMoneyExact(item.new_value)}
              </>
            ) : null}
            {item.reason ? <span className="hint"> · “{item.reason}”</span> : null}
            {item.post_snapshot && (
              <span className="pill amber ms-2" title="This month was already in a snapshot taken before this edit. The snapshot is unchanged.">
                Changed after the last snapshot
              </span>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

export default function ProjectPage({ projectId, backLabel, onBack, chartOpen, onSessionEnded, onOpenDeal }: Props) {
  const ws = useWorkspace()
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [failed, setFailed] = useState(false)
  const [history, setHistory] = useState<History>({ kind: 'loading' })
  const [openFinding, setOpenFinding] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, FindingDetail>>({})
  const [findingTick, setFindingTick] = useState(0)
  const [checking, setChecking] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const data = ws.state.kind === 'ready' ? ws.state.data : null
  const row = ws.rows.find((r) => r.project.id === projectId) ?? null
  const canRun = data?.health?.can_run ?? false

  const fail = useCallback(
    (error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (error instanceof ApiError && error.status === 401) onSessionEnded()
      else setFailed(true)
    },
    [onSessionEnded],
  )

  // The detail and history refetch after every saved edit (revision), so the chart follows the backend.
  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    fetchProject(projectId, signal)
      .then((d) => {
        setDetail(d)
        setFailed(false)
        if (!d.can_view_history) return setHistory({ kind: 'hidden' })
        return fetchHistory(projectId, signal).then((page) => setHistory({ kind: 'ready', items: page.items }))
      })
      .catch(fail)
    return () => controller.abort()
  }, [projectId, ws.revision, fail])

  const findingIds = useMemo(() => (row ? row.findings.map((f) => f.id) : []), [row])
  const idsKey = findingIds.join(',')
  useEffect(() => {
    const controller = new AbortController()
    Promise.all(findingIds.map((id) => fetchFinding(id, controller.signal)))
      .then((list) => setDetails(Object.fromEntries(list.map((d) => [d.id, d]))))
      .catch(() => undefined)
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, findingTick])

  async function rerun() {
    setChecking(true)
    setMessage(null)
    try {
      await runChecks()
      await ws.reloadFindings()
      setFindingTick((n) => n + 1)
      setMessage({ ok: true, text: 'Checks re-run. Statuses are up to date.' })
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? error.message : 'The checks could not be run.' })
    } finally {
      setChecking(false)
    }
  }

  if (ws.state.kind === 'loading') return <Loading text="Loading the project…" />
  if (failed) return <ErrorState text="This project could not be loaded." onRetry={() => setFailed(false)} />
  if (!detail || !row) return <Loading text="Loading the project…" />

  const actions = row.findings.flatMap((f) => {
    const d = details[f.id]
    const steps = d?.investigations[0]?.result?.next_steps.map((s) => s.action) ?? []
    return [d?.suggested_fix, ...steps].filter((x): x is string => Boolean(x))
  })
  const uniqueActions = [...new Set(actions)]
  const facts: [string, string][] = [
    ['Customer', detail.customer],
    ['Practice', detail.practice ?? '—'],
    ['Region', detail.region ?? '—'],
    ['Industry', detail.industry ?? '—'],
    ['Billing', detail.billing_type ?? '—'],
    ['Client partner', detail.client_partner?.name ?? '—'],
    ['Commercial owner', detail.commercial_owner?.name ?? '—'],
    ['Total forecast', formatMoney(detail.total_revenue)],
    ['Last updated', detail.last_updated ? formatDate(detail.last_updated) : '—'],
  ]

  return (
    <>
      <button type="button" className="linkbtn back" onClick={onBack}>
        {icons.left}
        {backLabel}
      </button>
      <PageHeader
        pre={detail.name}
        title={
          <>
            {detail.customer} <span className={`rag ${STATUS_TONE[row.status]}`} style={{ verticalAlign: 'middle', marginLeft: 6 }}>{STATUS_LABEL[row.status]}</span>
          </>
        }
      />
      <div className="stack">
        {ws.notice && (
          <div className="notice-sticky">
            <Notice kind={ws.notice.kind} onDismiss={() => ws.setNotice(null)}>
              {ws.notice.text}
            </Notice>
          </div>
        )}
        <dl className="kv">
          {facts.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>

        <section className="panel" aria-labelledby="attention-title">
          <div className="panel-head">
            <div>
              <h2 id="attention-title">Needs attention</h2>
              <p className="hint">{row.findings.length === 0 ? 'Nothing is flagged for this project.' : `${row.findings.length} flagged by the checks`}</p>
            </div>
            {canRun ? (
              <button type="button" className="btn btn-sm" disabled={checking} onClick={() => void rerun()}>
                {checking ? 'Checking…' : 'Re-run checks'}
              </button>
            ) : (
              <span className="hint">Checks re-run every night. Sales leadership or Finance can run them now.</span>
            )}
          </div>
          {message && (
            <p className={message.ok ? 'text-success' : 'text-danger'} role={message.ok ? 'status' : 'alert'}>
              {message.text}
            </p>
          )}
          {row.status === 'missing_entry' && row.findings.length === 0 && (
            <ul className="flags">
              <li>
                <b>No forecast entered for this month</b>
                <p>Add this month’s revenue in the monthly forecast below.</p>
              </li>
            </ul>
          )}
          {row.findings.length > 0 ? (
            <ul className="flags calm">
              {row.findings.map((f) => (
                <li key={f.id} className={f.severity === 'error' ? 'high' : ''}>
                  <b>{f.title}</b>
                  <p>{tidyMoney(f.summary)}</p>
                  <small>
                    {RULE_LABELS[f.rule_code] ?? f.rule_label} · {formatMoney(f.financial_impact)} at stake
                    {f.status !== 'open' ? ` · ${f.status === 'accepted' ? 'Accepted' : 'Dismissed'}, still detected` : ''}
                  </small>
                  <div className="flag-actions">
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => setOpenFinding(f.id)}>
                      Open Revenue Detective
                    </button>
                    <span className={`pill ${SEVERITY_TONE[f.severity]}`}>{f.severity === 'error' ? 'High' : 'Medium'}</span>
                    {f.awaiting_review && <span className="pill amber">Review the investigation</span>}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            row.status === 'on_track' && (
              <p className="ok-note">
                {icons.check}
                On track: the checks have nothing to report.
              </p>
            )
          )}
          {row.clearedFindings.length > 0 && (
            <p className="hint mt-3">
              {row.clearedFindings.length} reviewed finding{row.clearedFindings.length > 1 ? 's' : ''} no longer detected by the checks.
            </p>
          )}
        </section>

        {uniqueActions.length > 0 && (
          <section className="panel">
            <div className="panel-head">
              <h2>Recommended actions</h2>
            </div>
            <ol className="det-steps">
              {uniqueActions.map((a) => (
                <li key={a}>
                  <span>{a}</span>
                </li>
              ))}
            </ol>
          </section>
        )}

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Monthly forecast</h2>
              <p className="hint">{detail.can_edit ? 'Change any month. Each change is saved and recorded in the history.' : 'Current forecast by month.'}</p>
            </div>
            {ws.pending.size > 0 && (
              <span className="saving-indicator" role="status">
                <span className="spinner-border spinner-border-sm" aria-hidden="true" /> Saving…
              </span>
            )}
          </div>
          <MonthlyForecast detail={detail} months={data?.months ?? []} currentMonth={ws.currentMonth} saveAmount={ws.saveAmount} />
        </section>

        <Fold title="Monthly revenue" hint="Original import and the current forecast, month by month." defaultOpen={chartOpen}>
          <ProjectChart detail={detail} months={data?.months ?? []} currentMonth={ws.currentMonth} />
        </Fold>

        <Fold title="Change history" hint="Every edit, who made it and why.">
          <HistoryList history={history} />
        </Fold>
      </div>

      {openFinding !== null && (
        <DetectiveDrawer
          findingId={openFinding}
          onClose={() => setOpenFinding(null)}
          onChanged={() => {
            void ws.reloadFindings()
            setFindingTick((n) => n + 1)
          }}
          onSessionEnded={onSessionEnded}
          onOpenDeal={(deal) => {
            setOpenFinding(null)
            onOpenDeal(deal)
          }}
        />
      )}
    </>
  )
}
