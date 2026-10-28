import { useEffect, useRef } from 'react'
import {
  CategoryScale,
  Chart,
  Filler,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
  type Plugin,
} from 'chart.js'
import { formatMoney, formatMoneyShort } from '../format'
import { fontFamily, token, withAlpha } from './theme'

Chart.register(CategoryScale, LinearScale, LineController, LineElement, PointElement, Filler, Tooltip)

export type LineSeries = {
  label: string
  data: (number | null)[]
  /** A CSS token from styles.css, e.g. `--brand`. */
  color: string
  dashed?: boolean
  fill?: boolean
}

type Props = {
  labels: string[]
  series: LineSeries[]
  ariaLabel: string
  /** Index of the current month: a dashed "Today" marker is drawn there. */
  markerIndex?: number | null
  size?: 'short' | 'default' | 'tall'
}

/** The reference's "Today" marker, drawn on the chart canvas. */
function todayMarker(index: number, color: string): Plugin<'line'> {
  return {
    id: 'todayMarker',
    afterDatasetsDraw(chart) {
      const x = chart.scales.x?.getPixelForValue(index)
      if (x === undefined) return
      const { ctx, chartArea } = chart
      ctx.save()
      ctx.setLineDash([3, 3])
      ctx.strokeStyle = color
      ctx.globalAlpha = 0.55
      ctx.beginPath()
      ctx.moveTo(x, chartArea.top)
      ctx.lineTo(x, chartArea.bottom)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.globalAlpha = 1
      ctx.fillStyle = color
      ctx.font = `700 11px ${fontFamily()}`
      ctx.fillText('Today', x + 5, chartArea.top + 10)
      ctx.restore()
    },
  }
}

/** A line chart of figures the API returned. Nothing is calculated here beyond formatting. */
export default function LineChart({ labels, series, ariaLabel, markerIndex = null, size = 'default' }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!canvas.current) return
    const muted = token('--muted')
    const grid = token('--line')
    const chart = new Chart(canvas.current, {
      type: 'line',
      data: {
        labels,
        datasets: series.map((s) => {
          const color = token(s.color)
          return {
            label: s.label,
            data: s.data,
            borderColor: color,
            backgroundColor: s.fill ? withAlpha(color, 0.12) : color,
            borderWidth: 2.5,
            borderDash: s.dashed ? [6, 4] : [],
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: color,
            tension: 0.25,
            fill: s.fill ? 'origin' : false,
            spanGaps: false,
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
            callbacks: { label: (item) => `${item.dataset.label}: ${formatMoney(item.parsed.y)}` },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkipPadding: 12 } },
          y: {
            beginAtZero: true,
            grid: { color: grid },
            border: { display: false },
            ticks: { color: muted, callback: (value) => formatMoneyShort(Number(value)) },
          },
        },
      },
      plugins: markerIndex === null ? [] : [todayMarker(markerIndex, token('--ink'))],
    })
    return () => chart.destroy()
  }, [labels, series, markerIndex])

  return (
    <div className={`chart-wrap ${size === 'tall' ? 'tall' : size === 'short' ? 'short' : ''}`} role="img" aria-label={ariaLabel}>
      <canvas ref={canvas} />
    </div>
  )
}
