import {
  BarController, BarElement, CategoryScale, Chart, Filler, Legend, LinearScale,
  LineController, LineElement, PointElement, Tooltip,
} from 'chart.js'
import { useEffect, useState } from 'react'

Chart.register(BarController, BarElement, CategoryScale, Filler, Legend, LinearScale,
  LineController, LineElement, PointElement, Tooltip)

// Chart colours come from CSS custom properties so light/dark mode stay in one place.
const VARS = ['--series-heat', '--series-line', '--series-ink', '--grid', '--text-secondary',
  '--text-muted', '--surface-1', '--text-primary', '--border']

function readVars() {
  const cs = getComputedStyle(document.documentElement)
  return Object.fromEntries(VARS.map((v) => [v.slice(2), cs.getPropertyValue(v).trim()]))
}

export function useThemeColors() {
  const [colors, setColors] = useState(readVars)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => setColors(readVars())
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])
  return colors
}

export function baseOptions(c) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false }, // we render an HTML legend next to the chart
      tooltip: {
        backgroundColor: c['surface-1'], titleColor: c['text-primary'], bodyColor: c['text-secondary'],
        borderColor: c.border, borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true,
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: c['text-muted'], maxRotation: 0 }, border: { color: c.grid } },
      y: { grid: { color: c.grid }, ticks: { color: c['text-muted'] }, border: { display: false }, beginAtZero: true },
    },
  }
}
