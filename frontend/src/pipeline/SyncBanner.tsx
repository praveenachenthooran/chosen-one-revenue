import type { SyncStatus } from '../api'

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : 'never'
}

type Props = {
  status: SyncStatus | null
  statusError: boolean
  syncing: boolean
  onSync: () => void
}

/** Shows how fresh the HubSpot data is. A failed or stale sync never blocks the forecast. */
export default function SyncBanner({ status, statusError, syncing, onSync }: Props) {
  if (statusError) {
    return (
      <div className="sync-banner sync-warn" role="status">
        Could not check the HubSpot sync status. The forecast below is still available.
      </div>
    )
  }
  if (!status) return null

  let kind = 'sync-ok'
  let text = `Last synced ${when(status.last_success?.finished_at ?? null)} · ${status.last_success?.deals_seen ?? 0} deals`
  if (status.reason === 'never') {
    kind = 'sync-warn'
    text = 'No HubSpot sync has run yet, so there is no pipeline data.'
  } else if (status.reason === 'failed') {
    kind = 'sync-bad'
    text = `Sync stale since ${when(status.stale_since)}: the latest sync failed${
      status.last_run?.error_message ? ` (${status.last_run.error_message})` : ''
    }. Showing the last good data.`
  } else if (status.reason === 'old') {
    kind = 'sync-warn'
    text = `Sync stale since ${when(status.stale_since)}: older than ${status.stale_after_minutes} minutes.`
  }

  return (
    <div className={`sync-banner ${kind}`} role="status">
      <span>{text}</span>
      {status.can_sync && (
        <button type="button" className="btn btn-outline-secondary btn-sm" disabled={syncing} onClick={onSync}>
          {syncing ? 'Syncing…' : 'Sync now'}
        </button>
      )}
    </div>
  )
}
