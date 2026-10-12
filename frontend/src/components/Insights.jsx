const fmtP = (p) => (p < 0.001 ? 'p < 0.001' : `p = ${p.toFixed(3)}`)

function SigBadge({ item }) {
  if (!item) return null
  return item.significant
    ? <span className="badge sig">Significant · {fmtP(item.p_value)}</span>
    : <span className="badge">Not significant · {fmtP(item.p_value)}</span>
}

export function Headline({ result }) {
  const h = result.insights.headline
  const { meta } = result
  return (
    <section className="panel headline" aria-labelledby="headline-h">
      <div className="chart-head" style={{ marginBottom: 0 }}>
        <span className="eyebrow" id="headline-h">
          Heatwaves · {meta.region.label} · {meta.start_year}–{meta.end_year}
        </span>
        <SigBadge item={h} />
      </div>
      <p className="big">{h ? h.text : 'Not enough complete years to test for a trend.'}</p>
    </section>
  )
}

export function StatTiles({ result }) {
  const { yearly, trends, threshold, events } = result
  const complete = yearly.filter((r) => r.complete)
  const avg = (k) => complete.reduce((a, r) => a + r[k], 0) / Math.max(1, complete.length)
  const sen = trends.events.sen
  const daysSen = trends.heatwave_days.sen
  const thrText = threshold.method === 'fixed'
    ? `${threshold.value.toFixed(1)} °C`
    : `${threshold.min.toFixed(0)}–${threshold.max.toFixed(0)} °C`

  return (
    <div className="tiles">
      <div className="tile">
        <span className="label">Threshold ({threshold.percentile}th pct)</span>
        <div className="value">{thrText}</div>
        <div className="sub">Daily max, {threshold.baseline[0]}–{threshold.baseline[1]} baseline</div>
      </div>
      <div className="tile">
        <span className="label">Heatwaves in range</span>
        <div className="value">{events.length}</div>
        <div className="sub">{avg('events').toFixed(1)} per year on average</div>
      </div>
      <div className="tile">
        <span className="label">Trend in events</span>
        <div className="value">{sen ? `${sen.slope_per_year * 10 >= 0 ? '+' : ''}${(sen.slope_per_year * 10).toFixed(2)}` : '–'}</div>
        <div className="sub">per decade (Sen's slope)</div>
      </div>
      <div className="tile">
        <span className="label">Trend in heatwave days</span>
        <div className="value">{daysSen ? `${daysSen.slope_per_year * 10 >= 0 ? '+' : ''}${(daysSen.slope_per_year * 10).toFixed(1)}` : '–'}</div>
        <div className="sub">days per decade · {trends.heatwave_days.mann_kendall ? fmtP(trends.heatwave_days.mann_kendall.p_value) : ''}</div>
      </div>
    </div>
  )
}

export function Details({ result }) {
  const { supporting, facts, caveats } = result.insights
  return (
    <div className="row" style={{ flexWrap: 'wrap', alignItems: 'stretch' }}>
      <section className="panel" style={{ minWidth: 280 }}>
        <h2>What else changed</h2>
        <ul className="list">
          {supporting.map((s) => (
            <li key={s.metric}>{s.text} <SigBadge item={s} /></li>
          ))}
          {facts.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      </section>
      <section className="panel caveats" style={{ minWidth: 280 }}>
        <h2>Read this with care</h2>
        <ul className="list">
          {caveats.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </section>
    </div>
  )
}
