import type { ReactNode } from 'react'

const stroke = { fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' } as const

const svg = (children: ReactNode, size?: number) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    {children}
  </svg>
)

export const icons = {
  list: (
    svg(<><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" /></>)
  ),
  chart: (
    svg(<><path d="M3 3v18h18" />
      <path d="M7 15l4-5 3 3 5-7" /></>)
  ),
  bars: (
    svg(<><path d="M5 21V10M12 21V4M19 21v-8" /></>)
  ),
  funnel: (
    svg(<><path d="M3 5h18l-7 8v6l-4-2v-4Z" /></>)
  ),
  shield: (
    svg(<><path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6Z" />
      <path d="M9 12l2 2 4-4" /></>)
  ),
  clock: (
    svg(<><circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" /></>)
  ),
  upload: (
    svg(<><path d="M12 16V4M6 10l6-6 6 6M4 20h16" /></>)
  ),
  target: (
    svg(<><circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" /></>)
  ),
  download: (
    svg(<><path d="M12 4v12M6 10l6 6 6-6M4 20h16" /></>)
  ),
  left: (
    svg(<><path d="M15 6l-6 6 6 6" /></>, 16)
  ),
  check: (
    svg(<><path d="M5 12l5 5 9-10" /></>, 16)
  ),
  chevron: (
    svg(<><path d="M6 9l6 6 6-6" /></>)
  ),
  search: (
    svg(<><circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" /></>)
  ),
  close: (
    svg(<><path d="M6 6l12 12M18 6L6 18" /></>)
  ),
}
