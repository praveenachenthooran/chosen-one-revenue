/** A Chart.js stand-in that records every chart drawn, so tests can assert on what is plotted. */
type Dataset = { label: string; data: (number | null)[]; backgroundColor?: unknown }
export type DrawnChart = {
  config: {
    type: string
    data: { labels: string[]; datasets: Dataset[] }
    plugins?: { id: string }[]
  }
  destroyed: boolean
}

export const charts: DrawnChart[] = []

class Chart {
  static register() {}
  config: DrawnChart['config']
  destroyed = false
  constructor(_canvas: unknown, config: DrawnChart['config']) {
    this.config = config
    charts.push(this as unknown as DrawnChart)
  }
  destroy() {
    this.destroyed = true
  }
}

const stub = {}
export const chartJsMock = {
  Chart,
  CategoryScale: stub,
  LinearScale: stub,
  LineController: stub,
  LineElement: stub,
  PointElement: stub,
  BarController: stub,
  BarElement: stub,
  Filler: stub,
  Legend: stub,
  Tooltip: stub,
}

/** The newest chart that is still on screen. */
export const liveChart = (): DrawnChart | undefined => [...charts].reverse().find((c) => !c.destroyed)
