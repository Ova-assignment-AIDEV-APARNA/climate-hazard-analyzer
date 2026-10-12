const FIRST_YEAR = 1950

function YearSelect({ label, value, min, max, onChange }) {
  const years = []
  for (let y = max; y >= min; y--) years.push(y)
  return (
    <label className="field" style={{ marginTop: 0 }}>
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {years.map((y) => <option key={y} value={y}>{y}</option>)}
      </select>
    </label>
  )
}

export default function SettingsPanel({ settings, onChange, lastYear, onRun, loading, error }) {
  const set = (k) => (v) => onChange({ ...settings, [k]: v })
  const span = settings.endYear - settings.startYear + 1
  const baseSpan = settings.baselineEnd - settings.baselineStart + 1
  const invalid = span < 10 ? 'Choose at least 10 years.' : baseSpan < 10 ? 'Baseline needs at least 10 years.' : ''

  return (
    <section className="panel" aria-labelledby="settings-h">
      <h2 id="settings-h">Analysis</h2>

      <label className="field">
        <span>Hazard</span>
        <select value={settings.hazard} onChange={(e) => set('hazard')(e.target.value)}>
          <option value="heatwave">Heatwave</option>
          <option value="drought" disabled>Drought (coming soon)</option>
          <option value="heavy_rain" disabled>Heavy rainfall (coming soon)</option>
        </select>
      </label>

      <div className="field">
        <span>Time range</span>
        <div className="row">
          <YearSelect label="From" value={settings.startYear} min={FIRST_YEAR} max={lastYear - 9}
            onChange={set('startYear')} />
          <YearSelect label="To" value={settings.endYear} min={FIRST_YEAR + 9} max={lastYear}
            onChange={set('endYear')} />
        </div>
      </div>

      <details className="advanced">
        <summary>Heatwave definition</summary>
        <p className="hint">
          A heatwave is {settings.minDays}+ consecutive days with daily maximum temperature above the{' '}
          {settings.percentile}th percentile of the {settings.baselineStart}–{settings.baselineEnd} baseline.
        </p>
        <div className="row">
          <label className="field"><span>Percentile</span>
            <select value={settings.percentile} onChange={(e) => set('percentile')(Number(e.target.value))}>
              {[90, 92.5, 95, 97.5, 99].map((p) => <option key={p} value={p}>{p}th</option>)}
            </select>
          </label>
          <label className="field"><span>Min. consecutive days</span>
            <select value={settings.minDays} onChange={(e) => set('minDays')(Number(e.target.value))}>
              {[2, 3, 4, 5, 6, 7].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        </div>
        <label className="field"><span>Threshold type</span>
          <select value={settings.method} onChange={(e) => set('method')(e.target.value)}>
            <option value="fixed">One threshold for the whole year</option>
            <option value="calendar_day">Per calendar day (unusual for the season)</option>
          </select>
        </label>
        <div className="field">
          <span>Baseline period for the percentile</span>
          <div className="row">
            <YearSelect label="From" value={settings.baselineStart} min={FIRST_YEAR} max={lastYear - 9}
              onChange={set('baselineStart')} />
            <YearSelect label="To" value={settings.baselineEnd} min={FIRST_YEAR + 9} max={lastYear}
              onChange={set('baselineEnd')} />
          </div>
        </div>
      </details>

      {invalid && <p className="hint" style={{ color: 'var(--danger)' }}>{invalid}</p>}
      <button className="btn btn-primary" type="button" onClick={onRun} disabled={loading || !!invalid}>
        {loading ? 'Analysing…' : 'Analyse heatwaves'}
      </button>
      {loading && <p className="hint">The first request for a new place downloads ~75 years of daily data and can take up to a minute.</p>}
      {error && <div className="error" role="alert" style={{ marginTop: 12 }}>{error}</div>}
    </section>
  )
}
