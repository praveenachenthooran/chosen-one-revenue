import { useEffect, useRef } from 'react'
import {
  CategoryScale,
  Chart,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'
import type { ForecastMonth } from '../api'
import { formatMoney, formatMonthLabel } from '../format'

Chart.register(CategoryScale, LinearScale, LineController, LineElement, PointElement, Legend, Tooltip)

/** Committed, weighted and combined revenue by month, drawn from the API's totals (a line chart,
 *  as the team asked). Values are only plotted here; every figure is calculated by the server. */
export default function ForecastChart({ months }: { months: ForecastMonth[] }) {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!canvas.current) return
    const series = (pick: (m: ForecastMonth) => string) => months.map((m) => Number(pick(m)))
    const chart = new Chart(canvas.current, {
      type: 'line',
      data: {
        labels: months.map((m) => formatMonthLabel(m.month)),
        datasets: [
          { label: 'Committed', data: series((m) => m.committed), borderColor: '#315be8', tension: 0.25 },
          { label: 'Weighted pipeline', data: series((m) => m.weighted), borderColor: '#d98a00', tension: 0.25 },
          { label: 'Combined', data: series((m) => m.total), borderColor: '#136c3a', borderDash: [6, 4], tension: 0.25 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: { y: { ticks: { callback: (value) => formatMoney(Number(value)) } } },
      },
    })
    return () => chart.destroy()
  }, [months])

  return (
    <div className="chart-box" role="img" aria-label="Line chart of committed, weighted and combined revenue by month">
      <canvas ref={canvas} />
    </div>
  )
}
