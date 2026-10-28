import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  decideFinding,
  fetchFinding,
  startInvestigation,
  type FindingDetail,
  type Investigation,
} from '../api'
import { formatMoney } from '../format'
import { CAUSE_LABELS, STATUS_LABELS } from './labels'

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '')
const POLL_MS = 2000

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** The evidence the engine cited, as readable facts (nested records are shown compactly). */
function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  return (
    <dl className="detail-list evidence-list">
      {Object.entries(evidence).map(([key, value]) => (
        <div key={key} className="d-contents">
          <dt>{key.replace(/_/g, ' ')}</dt>
          <dd className="evidence-value">{show(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

type Props = {
  findingId: number
  onClose: () => void
  onChanged: () => void
  onSessionEnded: () => void
  onOpenProject: (projectId: number) => void
  onOpenDeal: (deal: { id: number; title: string }) => void
}

export default function FindingPanel({ findingId, onClose, onChanged, onSessionEnded, onOpenProject, onOpenDeal }: Props) {
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

  return (
    <aside className="detail-panel finding-panel" aria-label={detail ? `Finding: ${detail.title}` : 'Finding'}>
      <div className="d-flex justify-content-between align-items-start gap-2 mb-2">
        <div>
          <h2 className="h6 mb-1">{detail?.title ?? 'Finding'}</h2>
          {detail && (
            <span className="small text-secondary">
              {detail.rule_label} · <span className={`sev sev-${detail.severity}`}>{detail.severity}</span> ·{' '}
              {STATUS_LABELS[detail.status]}
            </span>
          )}
        </div>
        <button type="button" className="btn-close" aria-label="Close finding" onClick={onClose} />
      </div>

      {failed && (
        <p className="text-danger small" role="alert">
          This finding could not be loaded.
        </p>
      )}
      {!detail && !failed && (
        <p className="text-secondary small" role="status">
          Loading…
        </p>
      )}

      {detail && (
        <>
          <p className="small">{detail.summary}</p>
          <dl className="detail-list small">
            <dt>financial impact</dt>
            <dd>{formatMoney(detail.financial_impact)}</dd>
            <dt>routed to</dt>
            <dd>{detail.routed_to_name ?? 'Unassigned: Sales leadership or an Administrator decides'}</dd>
            <dt>first raised</dt>
            <dd>{when(detail.created_at)}</dd>
          </dl>

          <div className="d-flex flex-wrap gap-2 mb-2">
            {detail.projects.map((p) => (
              <button key={p.project_id} type="button" className="btn btn-outline-secondary btn-sm" onClick={() => onOpenProject(p.project_id)}>
                Open {p.row_id} and its audit history
              </button>
            ))}
            {detail.deal && (
              <button
                type="button"
                className="btn btn-outline-secondary btn-sm"
                onClick={() => onOpenDeal({ id: detail.deal!.id, title: detail.deal!.name })}
              >
                Open HubSpot deal {detail.deal.hubspot_deal_id}
              </button>
            )}
          </div>

          <section aria-label="Suggested fix" className="mb-2">
            <h3 className="h6">Suggested fix</h3>
            <p className="small mb-0">{detail.suggested_fix}</p>
          </section>

          <details className="mb-2">
            <summary className="small fw-semibold">Evidence from the checks</summary>
            <Evidence evidence={detail.evidence} />
          </details>

          <section aria-label="Investigation" className="investigation">
            <div className="d-flex justify-content-between align-items-center mb-1">
              <h3 className="h6 mb-0">AI Revenue Detective</h3>
              {detail.can_investigate && !running && (
                <button type="button" className="btn btn-outline-primary btn-sm" disabled={busy} onClick={() => void investigate()}>
                  {latest ? 'Investigate again' : 'Investigate'}
                </button>
              )}
            </div>
            <p className="small text-secondary">
              Read-only: it explains what the records show and what they do not. It cannot change revenue or decide a finding.
              {!detail.ai_available && detail.ai_unavailable_reason ? ` Claude is not in use (${detail.ai_unavailable_reason}); the deterministic investigator is used.` : ''}
            </p>

            {!latest && <p className="small mb-0">Not investigated yet.</p>}
            {running && (
              <p className="small" role="status">
                <span className="spinner-border spinner-border-sm text-primary" aria-hidden="true" /> Investigating… looking through the
                project history, snapshots and HubSpot.
              </p>
            )}
            {latest?.status === 'failed' && (
              <p className="small text-danger" role="alert">
                The investigation failed: {latest.error_message}
              </p>
            )}
            {latest?.status === 'completed' && latest.result && (
              <div>
                <div className="d-flex flex-wrap gap-2 align-items-center small mb-2">
                  <span className="investigation-state">Completed</span>
                  {detail.awaiting_review && <span className="finding-state state-awaiting">Awaiting review</span>}
                  <span className={`mode-badge mode-${latest.mode}`}>{latest.mode === 'ai' ? `Claude (${latest.model})` : 'Deterministic checks'}</span>
                  <span className="text-secondary">confidence {latest.result.confidence}</span>
                </div>
                {latest.fallback_reason && <p className="small text-secondary">{latest.fallback_reason}</p>}
                <p className="small fw-semibold">{latest.result.summary}</p>
                <ul className="list-unstyled cause-list">
                  {latest.result.causes.map((c, i) => (
                    <li key={i} className={`cause cause-${c.status}`}>
                      <span className={`cause-chip chip-${c.status}`}>{CAUSE_LABELS[c.status]}</span> {c.statement}
                      {c.evidence_refs.length > 0 && (
                        <div className="evidence-refs">
                          {c.evidence_refs.map((ref) => (
                            <span key={ref} className="ref-chip">
                              {ref}
                            </span>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="small">
                  <strong>Financial impact:</strong> {formatMoney(latest.result.financial_impact.amount)}, {latest.result.financial_impact.basis}{' '}
                  <span className="text-secondary">(calculated, not estimated by the model)</span>
                </p>
                <h4 className="h6">Recommended next steps</h4>
                <ol className="small">
                  {latest.result.next_steps.map((s, i) => (
                    <li key={i}>
                      {s.action}
                      {s.who ? <span className="text-secondary"> ({s.who})</span> : null}
                    </li>
                  ))}
                </ol>
                {latest.result.limitations.length > 0 && (
                  <p className="small text-secondary">Limits: {latest.result.limitations.join(' ')}</p>
                )}
                <details>
                  <summary className="small">Investigation steps ({latest.steps.length})</summary>
                  <ol className="small">
                    {latest.steps.map((s, i) => (
                      <li key={i}>
                        {s.tool} {JSON.stringify(s.arguments)} → {s.outcome}
                      </li>
                    ))}
                  </ol>
                </details>
              </div>
            )}
          </section>

          <section aria-label="Review" className="review-box">
            <h3 className="h6">Decision</h3>
            {detail.status !== 'open' ? (
              <p className="small mb-0">
                {STATUS_LABELS[detail.status]}
                {detail.reviewed_by_name ? ` by ${detail.reviewed_by_name} on ${when(detail.reviewed_at)}` : ' automatically: the condition is no longer detected'}
                {detail.review_reason ? `: “${detail.review_reason}”` : '.'}
              </p>
            ) : detail.can_review ? (
              <>
                <label className="form-label small" htmlFor="review-reason">
                  Reason for your decision (required)
                </label>
                <textarea
                  id="review-reason"
                  className="form-control form-control-sm mb-2"
                  rows={2}
                  maxLength={500}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
                <div className="d-flex gap-2">
                  <button type="button" className="btn btn-primary btn-sm" disabled={busy || reason.trim().length < 3} onClick={() => void decide('accept')}>
                    Accept finding
                  </button>
                  <button type="button" className="btn btn-outline-secondary btn-sm" disabled={busy || reason.trim().length < 3} onClick={() => void decide('dismiss')}>
                    Dismiss finding
                  </button>
                </div>
                <p className="small text-secondary mt-2 mb-0">
                  Accepting records that the finding is real; it does not change revenue. Fix the forecast by editing the project.
                </p>
              </>
            ) : (
              <p className="small mb-0">
                Only {detail.routed_to_name ?? 'Sales leadership or an Administrator'}, someone who can edit the affected project, Sales
                leadership or an Administrator can decide this finding.
              </p>
            )}
            {message && (
              <p className={`small mt-2 mb-0 ${message.ok ? 'text-success' : 'text-danger'}`} role={message.ok ? 'status' : 'alert'}>
                {message.text}
              </p>
            )}
          </section>

          <section aria-label="Audit trail" className="mt-3">
            <h3 className="h6">Audit trail</h3>
            <ol className="history-list">
              {detail.events.map((e) => (
                <li key={e.id}>
                  <div className="small text-secondary">
                    <time dateTime={e.created_at}>{when(e.created_at)}</time> · {e.actor_label}
                  </div>
                  <div>{e.message}</div>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </aside>
  )
}
