// Thin wrapper around the backend API.
// VITE_API_URL is the deployed backend; in dev it is empty and Vite proxies /api.
const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

// Turn the UI's params object into a query string, skipping empty values.
export function toQuery(params) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') q.set(k, String(v))
  }
  return q.toString()
}

// Region + analysis settings -> the flat params the API expects.
export function analysisParams(region, settings) {
  const base = {
    region_name: region.name || '',
    hazard: settings.hazard,
    start_year: settings.startYear,
    end_year: settings.endYear,
    percentile: settings.percentile,
    min_days: settings.minDays,
    threshold_method: settings.method,
    baseline_start: settings.baselineStart,
    baseline_end: settings.baselineEnd,
  }
  if (region.type === 'box') {
    const b = region.bbox
    return { ...base, south: round(b.south), west: round(b.west), north: round(b.north), east: round(b.east) }
  }
  return { ...base, lat: round(region.lat), lon: round(region.lon) }
}

const round = (v) => Math.round(v * 1000) / 1000

export function apiUrl(path, params) {
  const q = params ? `?${toQuery(params)}` : ''
  return `${BASE}${path}${q}`
}

export async function getJSON(path, params, signal) {
  let res
  try {
    res = await fetch(apiUrl(path, params), { signal })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    throw new ApiError('Could not reach the server. It may be waking up (free tier); try again in a minute.', 0)
  }
  if (res.ok) return res.json()

  let message = `Request failed (${res.status}).`
  try {
    const body = await res.json()
    const detail = Array.isArray(body.detail) ? body.detail.join(' ') : body.detail
    if (detail) message = detail
  } catch { /* non-JSON error body */ }
  if (res.status === 429) {
    const wait = res.headers.get('Retry-After')
    message = `Too many requests. Try again${wait ? ` in ${wait} s` : ' shortly'}.`
  }
  throw new ApiError(message, res.status)
}
