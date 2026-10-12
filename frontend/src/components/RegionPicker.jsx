import { useEffect, useState } from 'react'
import { CircleMarker, MapContainer, Rectangle, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { getJSON } from '../api'

const MAX_SPAN = 10 // degrees; must match MAX_BBOX_SPAN_DEG on the backend

const spanOk = (b) => b.north - b.south <= MAX_SPAN && b.east - b.west <= MAX_SPAN

// Map clicks: point mode sets the point; box mode takes two corner clicks.
function ClickHandler({ mode, onPoint, onBox }) {
  const [corner, setCorner] = useState(null)
  useEffect(() => setCorner(null), [mode])
  useMapEvents({
    click(e) {
      const { lat, lng } = e.latlng
      const lon = ((lng + 540) % 360) - 180 // wrap longitudes from a panned world map
      if (mode === 'point') return onPoint(lat, lon)
      if (mode === 'box') {
        if (!corner) return setCorner([lat, lon])
        onBox({
          south: Math.min(corner[0], lat), north: Math.max(corner[0], lat),
          west: Math.min(corner[1], lon), east: Math.max(corner[1], lon),
        })
        setCorner(null)
      }
    },
  })
  return corner ? <CircleMarker center={corner} radius={5} pathOptions={{ color: '#c2410c' }} /> : null
}

// Re-centre the map when the region changes from outside (search, typed coords).
function FitTo({ region }) {
  const map = useMap()
  useEffect(() => {
    if (region.type === 'box') {
      const b = region.bbox
      map.fitBounds([[b.south, b.west], [b.north, b.east]], { padding: [20, 20], maxZoom: 9 })
    } else {
      map.setView([region.lat, region.lon], Math.max(map.getZoom(), 6))
    }
  }, [region.key]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

let keyCounter = 0
export const makeRegion = (r) => ({ ...r, key: ++keyCounter })

export default function RegionPicker({ region, onChange }) {
  const [mode, setMode] = useState('search')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  async function search(e) {
    e.preventDefault()
    if (query.trim().length < 2) return
    setSearching(true)
    setSearchError('')
    try {
      const data = await getJSON('/api/geocode', { q: query.trim() })
      setResults(data.results)
      if (!data.results.length) setSearchError('No places found.')
    } catch (err) {
      setSearchError(err.message)
    } finally {
      setSearching(false)
    }
  }

  const shortName = (name) => name.split(',').slice(0, 2).join(',').trim()

  const setPoint = (lat, lon, name = '') =>
    onChange(makeRegion({ type: 'point', lat, lon, name: name || `${lat.toFixed(2)}, ${lon.toFixed(2)}` }))
  const setBox = (bbox, name = '') =>
    onChange(makeRegion({ type: 'box', bbox, name: name || 'Custom box' }))

  const editBox = (k, v) => {
    const bbox = { ...region.bbox, [k]: Number(v) }
    onChange({ ...region, bbox, name: 'Custom box' })
  }

  return (
    <section className="panel" aria-labelledby="region-h">
      <h2 id="region-h">Region</h2>
      <div className="segmented" role="group" aria-label="How to choose a region" style={{ marginTop: 10 }}>
        {[['search', 'Search place'], ['point', 'Click a point'], ['box', 'Draw a box']].map(([m, label]) => (
          <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>{label}</button>
        ))}
      </div>

      {mode === 'search' && (
        <form onSubmit={search} className="field">
          <span>City, state or country</span>
          <div className="row" style={{ gap: 6 }}>
            <input type="search" value={query} maxLength={100} placeholder="e.g. Hyderabad, Rajasthan"
              onChange={(e) => setQuery(e.target.value)} aria-label="Place name" style={{ flex: 3 }} />
            <button className="btn" type="submit" disabled={searching} style={{ flex: 1, justifyContent: 'center' }}>
              {searching ? '…' : 'Search'}
            </button>
          </div>
          {searchError && <p className="hint" role="status">{searchError}</p>}
          {results.length > 0 && (
            <ul className="search-results">
              {results.map((r, i) => {
                const fits = spanOk(r.bbox)
                return (
                  <li key={i}>
                    <div>{shortName(r.name)} <span className="kind">· {r.kind}</span></div>
                    <div className="actions">
                      <button className="btn" type="button" onClick={() => setPoint(r.lat, r.lon, shortName(r.name))}>
                        Use centre point
                      </button>
                      <button className="btn" type="button" disabled={!fits}
                        title={fits ? '' : `Area is larger than ${MAX_SPAN}° — draw a smaller box`}
                        onClick={() => setBox(r.bbox, shortName(r.name))}>
                        Use whole area
                      </button>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </form>
      )}
      {mode === 'point' && <p className="hint">Click the map to choose a location.</p>}
      {mode === 'box' && <p className="hint">Click two opposite corners on the map. Max {MAX_SPAN}° per side.</p>}

      <div className="map">
        <MapContainer center={[20.5, 78.9]} zoom={4} style={{ height: '100%' }} worldCopyJump>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <ClickHandler mode={mode} onPoint={(la, lo) => setPoint(la, lo)} onBox={(b) => setBox(b)} />
          {region.type === 'point' && (
            <CircleMarker center={[region.lat, region.lon]} radius={7}
              pathOptions={{ color: '#fff', weight: 2, fillColor: '#c2410c', fillOpacity: 1 }} />
          )}
          {region.type === 'box' && (
            <Rectangle bounds={[[region.bbox.south, region.bbox.west], [region.bbox.north, region.bbox.east]]}
              pathOptions={{ color: '#c2410c', weight: 2, fillOpacity: 0.08 }} />
          )}
          <FitTo region={region} />
        </MapContainer>
      </div>

      <div className="selected-region" aria-live="polite">
        <span className="label">Selected</span>
        <strong>{region.name}</strong>
        {region.type === 'point' ? (
          <div className="row" style={{ marginTop: 6 }}>
            <label className="field" style={{ marginTop: 0 }}><span>Latitude</span>
              <input type="number" step="0.01" min="-90" max="90" value={region.lat}
                onChange={(e) => onChange({ ...region, lat: Number(e.target.value), name: 'Custom point' })} />
            </label>
            <label className="field" style={{ marginTop: 0 }}><span>Longitude</span>
              <input type="number" step="0.01" min="-180" max="180" value={region.lon}
                onChange={(e) => onChange({ ...region, lon: Number(e.target.value), name: 'Custom point' })} />
            </label>
          </div>
        ) : (
          <>
            <div className="row" style={{ marginTop: 6 }}>
              {['south', 'north'].map((k) => (
                <label key={k} className="field" style={{ marginTop: 0 }}><span style={{ textTransform: 'capitalize' }}>{k}</span>
                  <input type="number" step="0.01" min="-90" max="90" value={round2(region.bbox[k])}
                    onChange={(e) => editBox(k, e.target.value)} />
                </label>
              ))}
            </div>
            <div className="row">
              {['west', 'east'].map((k) => (
                <label key={k} className="field"><span style={{ textTransform: 'capitalize' }}>{k}</span>
                  <input type="number" step="0.01" min="-180" max="180" value={round2(region.bbox[k])}
                    onChange={(e) => editBox(k, e.target.value)} />
                </label>
              ))}
            </div>
            {!spanOk(region.bbox) && <p className="hint" style={{ color: 'var(--danger)' }}>Box is larger than {MAX_SPAN}° on a side.</p>}
          </>
        )}
      </div>
    </section>
  )
}

const round2 = (v) => Math.round(v * 100) / 100
