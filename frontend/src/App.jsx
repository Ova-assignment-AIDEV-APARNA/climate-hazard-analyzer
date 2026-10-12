import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { analysisParams, getJSON, toQuery } from './api'
import { Details, Headline, StatTiles } from './components/Insights'
import RegionPicker, { makeRegion } from './components/RegionPicker'
import SettingsPanel from './components/SettingsPanel'
import { DataPanel, EventsTable } from './components/Tables'
import TrendChart from './components/TrendChart'
import YearExplorer from './components/YearExplorer'

const DEFAULT_SETTINGS = {
  hazard: 'heatwave', startYear: 1990, endYear: 2020, percentile: 95, minDays: 3,
  method: 'fixed', baselineStart: 1991, baselineEnd: 2020,
}
const DEFAULT_REGION = { type: 'point', lat: 17.385, lon: 78.487, name: 'Hyderabad, Telangana' }

// Restore state from the URL so an analysis can be shared as a link.
function fromUrl() {
  const q = new URLSearchParams(window.location.search)
  const num = (k) => (q.has(k) && !Number.isNaN(Number(q.get(k))) ? Number(q.get(k)) : undefined)
  let region = DEFAULT_REGION
  if (num('lat') !== undefined && num('lon') !== undefined) {
    region = { type: 'point', lat: num('lat'), lon: num('lon'), name: q.get('region_name') || 'Shared point' }
  } else if (['south', 'west', 'north', 'east'].every((k) => num(k) !== undefined)) {
    region = { type: 'box', bbox: { south: num('south'), west: num('west'), north: num('north'), east: num('east') },
      name: q.get('region_name') || 'Shared box' }
  }
  const settings = { ...DEFAULT_SETTINGS }
  const map = { start_year: 'startYear', end_year: 'endYear', percentile: 'percentile', min_days: 'minDays',
    baseline_start: 'baselineStart', baseline_end: 'baselineEnd' }
  for (const [k, s] of Object.entries(map)) if (num(k) !== undefined) settings[s] = num(k)
  if (q.get('threshold_method') === 'calendar_day') settings.method = 'calendar_day'
  return { region: makeRegion(region), settings }
}

export default function App() {
  const initial = useMemo(fromUrl, [])
  const [region, setRegion] = useState(initial.region)
  const [settings, setSettings] = useState(initial.settings)
  const [lastYear, setLastYear] = useState(new Date().getFullYear() - 1)
  const [result, setResult] = useState(null)
  const [resultParams, setResultParams] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [year, setYear] = useState(null)
  const abortRef = useRef(null)
  const explorerRef = useRef(null)

  useEffect(() => {
    getJSON('/api/health').then((h) => setLastYear(h.last_complete_year)).catch(() => {})
  }, [])

  const run = useCallback(async () => {
    abortRef.current?.abort() // a newer request replaces an older one
    const ctrl = new AbortController()
    abortRef.current = ctrl
    const params = analysisParams(region, settings)
    setLoading(true)
    setError('')
    try {
      const data = await getJSON('/api/analyze', params, ctrl.signal)
      setResult(data)
      setResultParams(params)
      // Start the day-by-day view on the year with the most heatwave days.
      const best = data.yearly.reduce((a, r) => ((r.heatwave_days ?? -1) > (a.heatwave_days ?? -1) ? r : a), data.yearly[0])
      setYear(best.year)
      window.history.replaceState(null, '', `?${toQuery(params)}`)
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message)
    } finally {
      if (abortRef.current === ctrl) setLoading(false)
    }
  }, [region, settings])

  useEffect(() => { run() }, []) // analyse the default (or shared) region on first load

  const showYear = (y) => {
    setYear(y)
    explorerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <>
      <header className="app-header">
        <h1>Heatwave Trend Analyzer</h1>
        <p>How often, how long and how intense heatwaves have been, from 75 years of reanalysis data.</p>
      </header>
      <div className="layout">
        <aside className="sidebar">
          <RegionPicker region={region} onChange={setRegion} />
          <SettingsPanel settings={settings} onChange={setSettings} lastYear={lastYear}
            onRun={run} loading={loading} error={error} />
        </aside>

        <main className="main" aria-busy={loading}>
          {!result && loading && (<><div className="skeleton" /><div className="skeleton" style={{ height: 320 }} /></>)}
          {!result && !loading && (
            <div className="panel empty">
              <h2>Choose a region and run the analysis</h2>
              <p>Search for a city or state, click a point on the map, or draw a box.</p>
            </div>
          )}
          {result && (
            <div style={{ opacity: loading ? 0.55 : 1, transition: 'opacity .2s', display: 'flex',
              flexDirection: 'column', gap: 20, minWidth: 0 }}>
              <Headline result={result} />
              <StatTiles result={result} />
              <TrendChart result={result} onYearClick={showYear} />
              <div ref={explorerRef}>
                {year && (
                  <YearExplorer params={resultParams} year={year} onYearChange={setYear}
                    years={result.yearly.map((r) => r.year)} />
                )}
              </div>
              <Details result={result} />
              <div className="row" style={{ flexWrap: 'wrap', alignItems: 'start' }}>
                <div style={{ flex: '2 1 380px', minWidth: 0 }}>
                  <EventsTable events={result.events} onYearClick={showYear} />
                </div>
                <div style={{ flex: '1 1 300px', minWidth: 0 }}>
                  <DataPanel result={result} params={resultParams} />
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
      <footer className="app-footer">
        Data: <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a> (ECMWF ERA5, CC BY 4.0) ·
        Place search: <a href="https://nominatim.openstreetmap.org/" target="_blank" rel="noreferrer">OpenStreetMap Nominatim</a>
      </footer>
    </>
  )
}
