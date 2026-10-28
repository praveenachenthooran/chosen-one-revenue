/** The hex mark from the Biggie Rev reference. */
export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 2 29 9.5v13L16 30 3 22.5v-13Z" fill="currentColor" opacity=".18" />
      <path d="M16 2 29 9.5 16 17Z" fill="currentColor" opacity=".9" />
      <path d="M3 9.5 16 17v13L3 22.5Z" fill="currentColor" opacity=".55" />
      <path d="M29 9.5v13L16 30V17Z" fill="currentColor" opacity=".75" />
    </svg>
  )
}
