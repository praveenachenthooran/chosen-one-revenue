/** Chart colours come from the design tokens in styles.css, so charts follow light and dark themes. */
export function token(name: string): string {
  if (typeof document === 'undefined') return ''
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#0d6b72'
}

export function fontFamily(): string {
  return token('--font') || 'system-ui, sans-serif'
}

/** `#rrggbb` -> `rgb(r g b / a)`; other colour strings are returned untouched. */
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(color)
  if (!m) return color
  const n = parseInt(m[1], 16)
  return `rgb(${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255} / ${alpha})`
}
