export type LegendItem = { label: string; color: string; kind?: 'bar' | 'line'; dashed?: boolean }

/** One legend style for every chart (matches the reference). `color` is a CSS token such as `--brand`. */
export default function ChartLegend({ items }: { items: LegendItem[] }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span key={item.label}>
          <i className={item.kind === 'line' ? 'line' : ''} style={{ background: `var(${item.color})`, opacity: item.dashed ? 0.7 : 1 }} />
          {item.label}
        </span>
      ))}
    </div>
  )
}
