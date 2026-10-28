import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  decideFinding,
  fetchFinding,
  startInvestigation,
  type EvidenceCause,
  type FindingDetail,
  type Investigation,
} from '../api'
import { formatDate, formatMoney } from '../format'
import { Drawer } from '../ui/Panel'
import { describeStep, evidenceRows } from './evidence'
import { STATUS_LABELS } from './labels'

const POLL_MS = 2000
const SEVERITY_LABEL = { error: 'High', warning: 'Medium', info: 'Low' } as const
const SEVERITY_TONE = { error: 'red', warning: 'amber', info: '' } as const
const GROUPS: { status: EvidenceCause['status']; title: string }[] = [
  { status: 'confirmed', title: 'Confirmed' },
  { status: 'hypothesis', title: 'Hypothesis' },
  { status: 'insufficient_evidence', title: 'Insufficient evidence' },
]

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '')

type Props = {
  findingId: number
  onClose: () => void
  onChanged: () => void
  onSessionEnded: () => void
  onOpenProject?: (projectId: number) => void
  onOpenDeal?: (deal: { id: number; title: string }) => void
}

function CauseGroup({ cause }: { cause: EvidenceCause }) {
  return (
    <li className={`det-item ${cause.status}`}>
      {cause.statement}
      {cause.evidence_refs.length > 0 && (
        <details>
          <summary className="hint">Sources ({cause.evidence_refs.length})</summary>
          <p className="hint">{cause.evidence_refs.join(', ')}</p>
        </details>
      )}
    </li>
  )
}

function Result({ latest }: { latest: Investigation }) {
  const result = latest.result!
  return (
    <>
      <p className="det-summary">{result.summary}</p>
      <div className="det-impact">
        <b>{formatMoney(result.financial_impact.amount)}</b>
        <span className="hint">{result.financial_impact.basis}. Calculated from the records, not estimated by the model.</span>
      </div>
      {GROUPS.map(({ status, title }) => {
        const items = result.causes.filter((c) => c.status === status)
        if (items.length === 0) return null
        return (
          <div key={status}>
            <p className="det-sub">
              <span className={`tag ${status}`}>{title}</span>
            </p>
            <ul className="det-list">
              {items.map((c, i) => (
                <CauseGroup key={i} cause={c} />
              ))}
            </ul>
          </div>
        )
      })}
      {result.next_steps.length > 0 && (
        <div>
          <p className="det-sub">Recommended actions</p>
          <ol className="det-steps">
            {result.next_steps.map((s, i) => (
              <li key={i}>
                <span>
                  {s.action}
                  {s.who ? <span className="hint"> ({s.who})</span> : null}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
      {result.limitations.length > 0 && (
        <details>
          <summary className="hint">What this can’t tell us</summary>
          <ul className="hint">
            {result.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  )
}

/** The AI Revenue Detective for one finding. Business meaning first; evidence and history on request. */
export default function DetectiveDrawer({ findingId, onClose, onChanged, onSessionEnded, onOpenProject, onOpenDeal }: Props) {
  const [detail, setDetail] = useState<FindingDetail | null>(null)
  const [failed, setFailed] = useState(false)
  const [tick, setTick] = useState(0)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const handle = useCallback(
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
    fetchFinding(findingId, controller.signal)
      .then((d) => {
        setDetail(d)
        setFailed(false)
      })
      .catch((error: unknown) => {
        if (!handle(error)) setFailed(true)
      })
    return () => controller.abort()
  }, [findingId, tick, handle])

  const latest: Investigation | undefined = detail?.investigations[0]
  const running = latest?.status === 'investigating'
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setTick((n) => n + 1), POLL_MS)
    return () => clearInterval(timer)
  }, [running])
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) onChanged() // the list shows the new state (awaiting review, failed)
    wasRunning.current = running
  }, [running, onChanged])

  async function investigate() {
    setBusy(true)
    setMessage(null)
    try {
      await startInvestigation(findingId)
      setTick((n) => n + 1)
    } catch (error) {
      if (!handle(error)) setMessage({ ok: false, text: error instanceof ApiError ? error.message : 'The investigation could not be started.' })
    } finally {
      setBusy(false)
    }
  }

  async function decide(decision: 'accept' | 'dismiss') {
    setBusy(true)
    setMessage(null)
    try {
      await decideFinding(findingId, decision, reason)
      setReason('')
      setMessage({ ok: true, text: decision === 'accept' ? 'Finding accepted. No revenue was changed.' : 'Finding dismissed.' })
      setTick((n) => n + 1)
      onChanged()
    } catch (error) {
      if (!handle(error)) setMessage({ ok: false, text: error instanceof ApiError ? error.message : 'The decision could not be saved.' })
    } finally {
      setBusy(false)
    }
  }

  const affected = detail?.projects[0]
  return (
    <Drawer
      title={detail?.title ?? 'Revenue Detective'}
      sub={affected ? `${affected.customer}: ${affected.name}` : detail?.rule_label}
      onClose={onClose}
    >
      {failed && (
        <p className="text-danger" role="alert">
          This finding could not be loaded.
        </p>
      )}
      {!detail && !failed && (
        <p className="hint" role="status">
          Loading…
        </p>
      )}

      {detail && (
        <>
          <div className="d-flex flex-wrap gap-2 align-items-center">
            <span className={`pill ${SEVERITY_TONE[detail.severity]}`}>{SEVERITY_LABEL[detail.severity]} severity</span>
            <span className="pill">{STATUS_LABELS[detail.status]}</span>
            {detail.awaiting_review && <span className="pill amber">Awaiting review</span>}
          </div>
          <p>{detail.summary}</p>
          <div className="det-impact">
            <b>{formatMoney(detail.financial_impact)}</b>
            <span className="hint">revenue at stake · {detail.routed_to_name ? `with ${detail.routed_to_name}` : 'unassigned'}</span>
          </div>

          <div className="flag-actions" style={{ marginTop: 0 }}>
            {onOpenProject &&
              detail.projects.map((p) => (
                <button key={p.project_id} type="button" className="btn btn-sm" onClick={() => onOpenProject(p.project_id)}>
                  Open {p.customer}
                </button>
              ))}
            {onOpenDeal && detail.deal && (
              <button type="button" className="btn btn-sm" onClick={() => onOpenDeal({ id: detail.deal!.id, title: detail.deal!.name })}>
                Open HubSpot deal
              </button>
            )}
          </div>

          <section className="detective" aria-label="Investigation">
            <div className="det-head">
              <div>
                <h3>AI Revenue Detective</h3>
                <p className="hint">Reads the records and explains them. It cannot change revenue or decide a finding.</p>
              </div>
              {detail.can_investigate && !running && (
                <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => void investigate()}>
                  {latest ? 'Investigate again' : 'Investigate'}
                </button>
              )}
            </div>

            {!latest && <p className="hint">Not investigated yet.</p>}
            {running && (
              <p role="status">
                <span className="spinner-border spinner-border-sm" aria-hidden="true" /> Investigating… looking through the project
                history, snapshots and HubSpot.
              </p>
            )}
            {latest?.status === 'failed' && (
              <p className="text-danger" role="alert">
                The investigation failed: {latest.error_message}
              </p>
            )}
            {latest?.status === 'completed' && latest.result && (
              <>
                <div className="d-flex flex-wrap gap-2 align-items-center">
                  <span className="pill brand" title={latest.model ?? undefined}>{latest.mode === 'ai' ? 'Claude' : 'Rule-based checks'}</span>
                  <span className="pill">{latest.result.confidence} confidence</span>
                </div>
                {latest.fallback_reason && <p className="hint">{latest.fallback_reason}</p>}
                <Result latest={latest} />
              </>
            )}
            {!detail.ai_available && detail.ai_unavailable_reason && latest?.mode !== 'ai' && (
              <p className="hint">Claude is not in use ({detail.ai_unavailable_reason}); the rule-based investigator is used.</p>
            )}
          </section>

          <section className="det-decision" aria-label="Review">
            <p className="det-sub">Your decision</p>
            {detail.status !== 'open' ? (
              <p>
                {STATUS_LABELS[detail.status]}
                {detail.reviewed_by_name
                  ? ` by ${detail.reviewed_by_name} on ${formatDate(detail.reviewed_at)}`
                  : ' automatically: the condition is no longer detected'}
                {detail.review_reason ? `: “${detail.review_reason}”` : '.'}
              </p>
            ) : detail.can_review ? (
              <>
                <label className="form-label" htmlFor="review-reason">
                  Reason for your decision (required)
                </label>
                <textarea
                  id="review-reason"
                  className="form-control mb-2"
                  rows={2}
                  maxLength={500}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
                <div className="d-flex gap-2 flex-wrap">
                  <button type="button" className="btn btn-primary btn-sm" disabled={busy || reason.trim().length < 3} onClick={() => void decide('accept')}>
                    Accept finding
                  </button>
                  <button type="button" className="btn btn-sm" disabled={busy || reason.trim().length < 3} onClick={() => void decide('dismiss')}>
                    Dismiss finding
                  </button>
                </div>
                <p className="hint mt-2 mb-0">Accepting records that the finding is real. It doesn’t change revenue or the project’s status; fix the forecast by editing the project.</p>
              </>
            ) : (
              <p className="hint mb-0">
                Only {detail.routed_to_name ?? 'Sales leadership or an Administrator'}, someone who can edit the affected project, Sales leadership or an
                Administrator can decide this finding.
              </p>
            )}
            {message && (
              <p className={`mt-2 mb-0 ${message.ok ? 'text-success' : 'text-danger'}`} role={message.ok ? 'status' : 'alert'}>
                {message.text}
              </p>
            )}
          </section>

          <details>
            <summary className="fw-semibold">View evidence</summary>
            <table className="evidence-table mt-2">
              <tbody>
                {evidenceRows(detail.evidence).map((row) => (
                  <tr key={row.label}>
                    <td>{row.label}</td>
                    <td>{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {detail.suggested_fix && (
              <p className="mt-2">
                <span className="det-sub">Suggested fix</span>
                <br />
                {detail.suggested_fix}
              </p>
            )}
            {latest && latest.steps.length > 0 && (
              <>
                <p className="det-sub mt-3">How this was checked</p>
                <ul className="det-list">
                  {latest.steps.map((s, i) => (
                    <li key={i} className="hint">
                      {describeStep(s.tool)}
                      {s.outcome ? `: ${s.outcome}` : ''}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </details>

          <details>
            <summary className="fw-semibold">History</summary>
            <ol className="timeline mt-2">
              {detail.events.map((e) => (
                <li key={e.id}>
                  <time dateTime={e.created_at}>
                    {when(e.created_at)} · {e.actor_label}
                  </time>
                  <div>{e.message}</div>
                </li>
              ))}
            </ol>
          </details>
        </>
      )}
    </Drawer>
  )
}
