import { useEffect, useState } from 'react'
import { ApiError, fetchStages, updateStage, type Stage } from '../api'

type Props = {
  isAdmin: boolean
  /** Called after a probability changes, so totals are re-read from the server. */
  onChanged: () => void
  onSessionEnded: () => void
}

/** The probability used for each HubSpot stage. Everyone can read it; Administrators edit it. */
export default function StagesPanel({ isAdmin, onChanged, onSessionEnded }: Props) {
  const [stages, setStages] = useState<Stage[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchStages(controller.signal)
      .then(setStages)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setFailed(true)
      })
    return () => controller.abort()
  }, [])

  async function save(stage: Stage) {
    const draft = drafts[stage.id]
    if (draft === undefined || Number(draft) === Number(stage.probability)) return
    try {
      const saved = await updateStage(stage.id, { probability: draft })
      setStages((current) => current?.map((s) => (s.id === saved.id ? saved : s)) ?? current)
      setDrafts((current) => Object.fromEntries(Object.entries(current).filter(([k]) => Number(k) !== stage.id)))
      setMessage({ ok: true, text: `Saved ${stage.name}: ${Math.round(Number(saved.probability) * 100)}%.` })
      onChanged()
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return onSessionEnded()
      setMessage({ ok: false, text: error instanceof ApiError ? error.message : 'Could not save the probability.' })
    }
  }

  return (
    <details className="stages-panel">
      <summary>Stage probabilities</summary>
      {failed && <p className="text-danger small mt-2">Could not load the stages.</p>}
      {stages && (
        <table className="table table-sm mt-2">
          <thead>
            <tr>
              <th>Stage</th>
              <th>In forecast</th>
              <th className="text-end">Probability</th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <tr key={stage.id}>
                <td>{stage.name}</td>
                <td>{stage.kind === 'lost' ? 'No' : stage.in_forecast ? 'Yes' : 'No'}</td>
                <td className="text-end">
                  {isAdmin && stage.kind !== 'lost' ? (
                    <input
                      className="form-control form-control-sm text-end stage-input"
                      inputMode="decimal"
                      aria-label={`Probability for ${stage.name}`}
                      value={drafts[stage.id] ?? stage.probability}
                      onChange={(event) => setDrafts((d) => ({ ...d, [stage.id]: event.target.value }))}
                      onBlur={() => void save(stage)}
                      onKeyDown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
                    />
                  ) : (
                    `${Math.round(Number(stage.probability) * 100)}%`
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!isAdmin && stages && (
        <p className="text-secondary small mb-0">Only an Administrator can change these (0 to 1, e.g. 0.6).</p>
      )}
      {message && (
        <p className={`small mb-0 ${message.ok ? 'text-success' : 'text-danger'}`} role={message.ok ? 'status' : 'alert'}>
          {message.text}
        </p>
      )}
    </details>
  )
}
