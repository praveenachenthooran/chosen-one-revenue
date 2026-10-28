import { useEffect, useRef } from 'react'
import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip, type Plugin } from 'chart.js'
import { formatMoney, formatMoneyShort } from '../format'
import { fontFamily, token, withAlpha } from './theme'

Chart.register(CategoryScale, LinearScale, BarController, BarElement, Tooltip)

export type BarSeries = {
  label: string
  data: (number | null)[]
  /** A CSS token from styles.css, e.g. `--forecast`. */
  color: string
  /** Forecast series are drawn lighter beyond the near-term window. Actuals never are. */
  fadeLongTerm?: boolean
}

type Props = {
  labels: string[]
  series: BarSeries[]
  ariaLabel: string
  /** Month indexes [first, last] of the near-term window (the next three months), if any. */
  nearTerm?: [number, number] | null
  size?: 'default' | 'tall'
}

function nearTermBand(range: [number, number], fill: string, text: string): Plugin<'bar'> {
  return {
    id: 'nearTermBand',
    beforeDatasetsDraw(chart) {
      const x = chart.scales.x
      if (!x) return
      const { ctx, chartArea } = chart
      const step = chartArea.width / chart.data.labels!.length
      const left = x.getPixelForValue(range[0]) - step / 2
      const right = x.getPixelForValue(range[1]) + step / 2
      ctx.save()
      ctx.fillStyle = fill
      ctx.fillRect(left, chartArea.top, right - left, chartArea.height)
      ctx.fillStyle = text
      ctx.font = `700 11px ${fontFamily()}`
      ctx.fillText('Next 3 months', left + 8, chartArea.top + 14)
      ctx.restore()
    },
  }
}

/** Grouped bars of figures the API returned (the estimated / actual / potential view the team asked for). */
export default function BarChart({ labels, series, ariaLabel, nearTerm = null, size = 'default' }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!canvas.current) return
    const muted = token('--muted')
    const chart = new Chart(canvas.current, {
      type: 'bar',
      data: {
        labels,
        datasets: series.map((s) => {
          const color = token(s.color)
          const faded = withAlpha(color, 0.55)
          return {
            label: s.label,
            data: s.data,
            backgroundColor: s.fadeLongTerm && nearTerm ? labels.map((_, i) => (i > nearTerm[1] ? faded : color)) : color,
            borderRadius: 3,
            maxBarThickness: 22,
          }
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        font: { family: fontFamily(), size: 12 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              afterTitle: (items) => {
                if (!nearTerm || !items[0]) return ''
                const i = items[0].dataIndex
                if (i >= nearTerm[0] && i <= nearTerm[1]) return 'Near-term forecast'
                return i > nearTerm[1] ? 'Longer-term forecast, less certain' : ''
              },
              label: (item) => (item.parsed.y === null ? '' : `${item.dataset.label}: ${formatMoney(item.parsed.y)}`),
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkipPadding: 8 } },
          y: {
            beginAtZero: true,
            grid: { color: token('--line') },
            border: { display: false },
            ticks: { color: muted, callback: (value) => formatMoneyShort(Number(value)) },
          },
        },
      },
      plugins: nearTerm ? [nearTermBand(nearTerm, withAlpha(token('--brand'), 0.07), token('--brand-strong'))] : [],
    })
    return () => chart.destroy()
  }, [labels, series, nearTerm])

  return (
    <div className={`chart-wrap ${size === 'tall' ? 'tall' : ''}`} role="img" aria-label={ariaLabel}>
      <canvas ref={canvas} />
    </div>
  )
}
