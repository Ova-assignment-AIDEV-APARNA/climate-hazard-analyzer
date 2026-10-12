import { useMemo, useState } from 'react'
import { Chart } from 'react-chartjs-2'
import { baseOptions, useThemeColors } from './chartSetup'

const METRIC_TABS = [
  ['events', 'Events'],
  ['heatwave_days', 'Heatwave days'],
  ['max_duration', 'Longest event'],
  ['cumulative_excess', 'Intensity'],
]

const UNITS = {
  events: 'events', heatwave_days: 'days', max_duration: 'days', cumulative_excess: '°C·days',
}

export default function TrendChart({ result, onYearClick }) {
  const [metric, setMetric] = useState('events')
  const [showTable, setShowTable] = useState(false)
  const c = useThemeColors()
  const { yearly, trends, metrics } = result
  const trend = trends[metric]
  const years = yearly.map((r) => r.year)

  const data = useMemo(() => {
    const sen = trend.sen
    return {
      labels: years,
      datasets: [
        {
          type: 'bar', label: 'Yearly value', data: yearly.map((r) => r[metric]),
          backgroundColor: c['series-heat'], maxBarThickness: 24,
          borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'bottom', order: 3,
        },
        {
          type: 'line', label: `${trend.rolling_window}-year rolling mean`, data: trend.rolling_mean,
          borderColor: c['series-line'], borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
          spanGaps: false, tension: 0.3, order: 1,
        },
        ...(sen ? [{
          type: 'line', label: "Sen's slope trend",
          data: years.map((y) => +(sen.intercept + sen.slope_per_year * y).toFixed(2)),
          borderColor: c['series-ink'], borderWidth: 1.5, borderDash: [6, 4], pointRadius: 0,
          pointHoverRadius: 0, order: 2,
        }] : []),
      ],
    }
  }, [result, metric, c]) // eslint-disable-line react-hooks/exhaustive-deps

  const options = useMemo(() => {
    const o = baseOptions(c)
    o.scales.y.title = { display: true, text: UNITS[metric], color: c['text-muted'] }
    o.scales.x.ticks.autoSkip = true
    o.scales.x.ticks.maxTicksLimit = 12
    o.plugins.tooltip.callbacks = {
      label: (ctx) => {
        const v = ctx.raw
        if (v === null || v === undefined) return `${ctx.dataset.label}: no data (incomplete year)`
        return `${ctx.dataset.label}: ${typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v}`
      },
      footer: () => 'Click a bar to see that year day by day',
    }
    o.onClick = (_, els) => {
      if (els.length) onYearClick(years[els[0].index])
    }
    o.onHover = (evt, els) => { evt.native.target.style.cursor = els.length ? 'pointer' : 'default' }
    return o
  }, [c, metric, result]) // eslint-disable-line react-hooks/exhaustive-deps

  const mk = trend.mann_kendall
  return (
    <section className="panel" aria-labelledby="trend-h">
      <div className="chart-head">
        <div>
          <h2 id="trend-h">{metrics[metric]} per year</h2>
          <p className="chart-sub">
            {mk ? `Mann-Kendall: ${mk.trend}, p = ${mk.p_value.toFixed(3)} · OLS R² = ${trend.ols.r_squared.toFixed(2)}`
              : trend.note}
          </p>
        </div>
        <div className="segmented metric-tabs" role="group" aria-label="Metric">
          {METRIC_TABS.map(([k, label]) => (
            <button key={k} type="button" aria-pressed={metric === k} onClick={() => setMetric(k)}>{label}</button>
          ))}
        </div>
      </div>

      {showTable ? (
        <div className="table-scroll">
          <table className="data">
            <caption className="sr-only">{metrics[metric]} by year</caption>
            <thead><tr><th>Year</th><th>Value</th><th>Rolling mean</th><th>Data coverage</th></tr></thead>
            <tbody>
              {yearly.map((r, i) => (
                <tr key={r.year}>
                  <td>{r.year}</td>
                  <td>{r[metric] ?? '–'}</td>
                  <td>{trend.rolling_mean[i] ?? '–'}</td>
                  <td>{Math.round(r.coverage * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-box" role="img"
          aria-label={`${metrics[metric]} per year, ${years[0]} to ${years.at(-1)}. ${mk ? `Trend ${mk.trend}.` : ''}`}>
          <Chart type="bar" data={data} options={options} />
        </div>
      )}

      <div className="chart-head" style={{ marginTop: 8, marginBottom: 0 }}>
        <div className="legend">
          <span><i className="sw-bar" />Yearly value</span>
          <span><i className="sw-line" />{trend.rolling_window}-year rolling mean</span>
          {trend.sen && <span><i className="sw-dash" />Sen's slope trend</span>}
        </div>
        <button className="link" type="button" onClick={() => setShowTable((s) => !s)}>
          {showTable ? 'Show chart' : 'Show as table'}
        </button>
      </div>
    </section>
  )
}
