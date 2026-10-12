import { useEffect, useMemo, useState } from 'react'
import { Chart } from 'react-chartjs-2'
import { getJSON } from '../api'
import { baseOptions, useThemeColors } from './chartSetup'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// Daily maximum temperature for one year, the threshold, and the days that
// belong to a heatwave. This is the "show your working" view of the detector.
export default function YearExplorer({ params, year, years, onYearChange }) {
  const [series, setSeries] = useState(null)
  const [error, setError] = useState('')
  const c = useThemeColors()

  useEffect(() => {
    const ctrl = new AbortController()
    setError('')
    getJSON('/api/series', { ...params, year }, ctrl.signal)
      .then(setSeries)
      .catch((e) => { if (e.name !== 'AbortError') setError(e.message) })
    return () => ctrl.abort()
  }, [params, year])

  const data = useMemo(() => {
    if (!series) return null
    return {
      labels: series.dates,
      datasets: [
        {
          label: 'Heatwave day', data: series.tmax.map((v, i) => (series.heatwave[i] ? v : null)),
          showLine: false, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: c['series-heat'],
          pointBorderColor: c['surface-1'], pointBorderWidth: 2, order: 1,
        },
        {
          label: 'Daily max temperature', data: series.tmax, borderColor: c['series-line'],
          borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 3, spanGaps: false, order: 2,
        },
        {
          label: 'Threshold', data: series.threshold_values, borderColor: c['series-ink'],
          borderWidth: 1.5, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 0, order: 3,
        },
      ],
    }
  }, [series, c])

  const options = useMemo(() => {
    const o = baseOptions(c)
    o.scales.y.beginAtZero = false
    o.scales.y.title = { display: true, text: '°C', color: c['text-muted'] }
    o.scales.x.ticks.autoSkip = false
    o.scales.x.ticks.callback = function (_, i) {
      const d = series?.dates[i]
      return d && d.endsWith('-01') ? MONTHS[Number(d.slice(5, 7)) - 1] : ''
    }
    o.plugins.tooltip.filter = (item) => item.raw !== null
    o.plugins.tooltip.callbacks = {
      label: (ctx) => (ctx.dataset.label === 'Heatwave day' ? ' Part of a heatwave'
        : `${ctx.dataset.label}: ${ctx.raw?.toFixed(1)} °C`),
    }
    return o
  }, [c, series])

  const hwDays = series ? series.heatwave.filter(Boolean).length : 0

  return (
    <section className="panel" aria-labelledby="year-h">
      <div className="chart-head">
        <div>
          <h2 id="year-h">Day by day in {year}</h2>
          <p className="chart-sub">
            {series ? `${hwDays} heatwave day${hwDays === 1 ? '' : 's'} · ${series.threshold.description}` : 'Loading…'}
          </p>
        </div>
        <label className="row" style={{ alignItems: 'center', gap: 8, flex: '0 0 auto' }}>
          <span className="label">Year</span>
          <select value={year} onChange={(e) => onYearChange(Number(e.target.value))} style={{ width: 110 }}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
      </div>
      {error && <div className="error">{error}</div>}
      {data && (
        <div className="chart-box" role="img" aria-label={`Daily maximum temperature in ${year} with ${hwDays} heatwave days`}>
          <Chart type="line" data={data} options={options} />
        </div>
      )}
      <div className="legend">
        <span><i className="sw-line" />Daily max temperature</span>
        <span><i className="sw-dash" />Threshold</span>
        <span><i className="sw-bar" style={{ borderRadius: '50%' }} />Heatwave day</span>
      </div>
    </section>
  )
}
