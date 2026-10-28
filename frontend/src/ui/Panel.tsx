import { useEffect, type ReactNode } from 'react'
import { icons } from './icons'

/** A collapsible panel: the summary row stays visible, the body opens on demand. */
export function Fold({
  title,
  hint,
  defaultOpen = false,
  children,
  id,
}: {
  title: string
  hint?: string
  defaultOpen?: boolean
  children: ReactNode
  id?: string
}) {
  return (
    <details className="panel panel-flush fold" open={defaultOpen} id={id}>
      <summary>
        <div>
          <h2>{title}</h2>
          {hint && <p className="hint">{hint}</p>}
        </div>
        <span className="chev">{icons.chevron}</span>
      </summary>
      <div className="fold-body">{children}</div>
    </details>
  )
}

export type Kpi = { label: string; value: string; note?: string; tone?: 'good' | 'bad' }

export function KpiStrip({ items }: { items: Kpi[] }) {
  return (
    <div className="kpis" style={{ ['--kpi-cols' as string]: items.length }}>
      {items.map((k) => (
        <div className="kpi" key={k.label}>
          <span>{k.label}</span>
          <b>{k.value}</b>
          {k.note && <small className={k.tone ?? ''}>{k.note}</small>}
        </div>
      ))}
    </div>
  )
}

export function PageHeader({
  title,
  sub,
  pre,
  actions,
}: {
  title: ReactNode
  sub?: string
  pre?: string
  actions?: ReactNode
}) {
  return (
    <div className="ph">
      <div>
        {pre && <p className="pre">{pre}</p>}
        <h1>{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  )
}

export function Loading({ text }: { text: string }) {
  return (
    <div className="state-box" role="status" aria-live="polite">
      <span className="spinner-border spinner-border-sm" aria-hidden="true" />
      <span>{text}</span>
    </div>
  )
}

export function ErrorState({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div className="state-box" role="alert">
      <p>{text}</p>
      {onRetry && (
        <button type="button" className="btn btn-primary btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

export function Notice({
  kind,
  children,
  onDismiss,
}: {
  kind: 'success' | 'error'
  children: ReactNode
  onDismiss?: () => void
}) {
  return (
    <div className={`notice notice-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <span>{children}</span>
      {onDismiss && <button type="button" className="btn-close btn-sm" aria-label="Dismiss message" onClick={onDismiss} />}
    </div>
  )
}

/** A right-hand drawer for contextual detail. Escape and the backdrop close it. */
export function Drawer({ title, sub, onClose, children }: { title: string; sub?: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="scrim" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head">
          <div>
            <h2>{title}</h2>
            {sub && <p className="hint">{sub}</p>}
          </div>
          <button type="button" className="btn-close" aria-label="Close" onClick={onClose} />
        </div>
        {children}
      </aside>
    </div>
  )
}
