import { useMemo, useState } from 'react'
import { apiUrl } from '../api'

const COLS = [
  ['start', 'Start'],
  ['duration', 'Days'],
  ['peak_tmax', 'Peak °C'],
  ['mean_excess', 'Mean excess °C'],
  ['cumulative_excess', '°C·days'],
]

export function EventsTable({ events, onYearClick }) {
  const [sort, setSort] = useState({ key: 'cumulative_excess', dir: -1 })
  const rows = useMemo(
    () => [...events].sort((a, b) => (a[sort.key] > b[sort.key] ? 1 : -1) * sort.dir),
    [events, sort],
  )
  const toggle = (key) => setSort((s) => ({ key, dir: s.key === key ? -s.dir : -1 }))

  return (
    <section className="panel" aria-labelledby="events-h">
      <h2 id="events-h">Heatwave events ({events.length})</h2>
      <p className="chart-sub">Sorted by intensity. Click a date to see that year.</p>
      {events.length === 0 ? <p className="hint">No events in this period.</p> : (
        <div className="table-scroll" style={{ marginTop: 8 }}>
          <table className="data">
            <thead>
              <tr>
                {COLS.map(([k, label]) => (
                  <th key={k} aria-sort={sort.key === k ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}>
                    <button type="button" onClick={() => toggle(k)}>
                      {label}{sort.key === k ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.start}>
                  <td><button className="link" type="button" onClick={() => onYearClick(e.year)}>{e.start}</button></td>
                  <td>{e.duration}</td>
                  <td>{e.peak_tmax.toFixed(1)}</td>
                  <td>{e.mean_excess.toFixed(1)}</td>
                  <td>{e.cumulative_excess.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function DataPanel({ result, params }) {
  const { quality, meta } = result
  const ds = meta.data_source
  const pts = meta.region.grid_points
  return (
    <section className="panel quality" aria-labelledby="data-h">
      <h2 id="data-h">Data &amp; export</h2>
      <p className="chart-sub">
        {ds.dataset} via {ds.name}, {ds.resolution}, {ds.record}.{' '}
        {pts.length === 1
          ? `Grid cell ${pts[0].lat.toFixed(2)}°, ${pts[0].lon.toFixed(2)}°${pts[0].elevation != null ? `, ${Math.round(pts[0].elevation)} m` : ''}.`
          : `Area-weighted mean of ${pts.length} grid cells.`}
      </p>
      <dl>
        <dt>Days in record{quality.points > 1 ? ' (per cell)' : ''}</dt><dd>{quality.total_days.toLocaleString()}</dd>
        <dt>Missing in source</dt><dd>{quality.missing_in_source}</dd>
        <dt>Impossible values removed</dt><dd>{quality.out_of_range_removed}</dd>
        <dt>One-day spikes removed</dt><dd>{quality.spikes_removed}</dd>
        <dt>Short gaps interpolated</dt><dd>{quality.interpolated}</dd>
        <dt>Still missing (breaks runs)</dt><dd>{quality.still_missing}</dd>
        <dt>Years under 90% coverage</dt><dd>{quality.incomplete_years.length ? quality.incomplete_years.join(', ') : 'none'}</dd>
        <dt>Served from cache</dt><dd>{result.cache?.analysis_cache === 'hit' ? 'yes' : 'no'}</dd>
      </dl>
      <div className="exports" style={{ marginTop: 14 }}>
        <a className="btn" href={apiUrl('/api/export.pdf', params)}>PDF report</a>
        <a className="btn" href={apiUrl('/api/export.csv', { ...params, table: 'yearly' })}>CSV: yearly</a>
        <a className="btn" href={apiUrl('/api/export.csv', { ...params, table: 'events' })}>CSV: events</a>
      </div>
    </section>
  )
}
