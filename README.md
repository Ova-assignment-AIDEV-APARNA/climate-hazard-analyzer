# Heatwave Trend Analyzer

A web app that detects heatwaves in 75 years of daily temperature data for any
point, place or lat-lon box, and reports whether they are becoming more
frequent, longer or more intense, with statistics that say how sure we can be.

- **Live app:** https://climate-hazard-analyzer-delta.vercel.app
- **API:** https://climate-hazard-analyzer-kx5n.onrender.com (health check: [`/api/health`](https://climate-hazard-analyzer-kx5n.onrender.com/api/health), interactive docs: [`/docs`](https://climate-hazard-analyzer-kx5n.onrender.com/docs))
- **Source:** https://github.com/Ova-assignment-AIDEV-APARNA/climate-hazard-analyzer

> The API runs on Render's free tier, which sleeps after ~15 minutes without
> traffic. The first request after that takes 30–60 seconds; later ones are fast.
> The first analysis of a new place also downloads ~75 years of daily data,
> which can take up to a minute; repeat requests come from the MongoDB cache.

<!-- To add a screenshot: save it as docs/screenshot.png, then replace this line with: ![App screenshot](docs/screenshot.png) -->

---

## What it does

1. Choose a region: search a city, state or country (OpenStreetMap), click a
   point on the map, or draw a box (Leaflet). Picking a new region runs the
   analysis straight away; if you then edit coordinates or settings by hand,
   a banner says the results are out of date and offers an **Update** button.
2. Choose a time range (1950 to the last complete year). Heatwave is the
   hazard, selected by default; drought and heavy rain are listed as planned.
3. The app shows:
   - a one-sentence headline finding, with its significance;
   - yearly charts for four metrics (events, heatwave days, longest event,
     intensity) with a rolling mean and a Sen's-slope trend line, plus a
     table view;
   - a day-by-day chart for any year, showing the threshold and which days
     were counted as heatwave days, so the detection can be checked by eye;
   - every event in a sortable table;
   - a data-quality report and caveats generated from the data;
   - PDF report and CSV exports.

The state of the page is kept in the URL, so an analysis can be shared as a link.

---

## Architecture

```
React (Vite) on Vercel                          FastAPI on Render
┌──────────────────────────┐   GET /api/...    ┌────────────────────────────────────────┐
│ RegionPicker (Leaflet)   │ ────────────────▶ │ rate limit ─▶ validate (Pydantic)      │
│ SettingsPanel            │                   │        │                               │
│ Headline / StatTiles     │ ◀──────────────── │        ▼                               │
│ TrendChart (Chart.js)    │   JSON / PDF /    │ AnalysisService                        │
│ YearExplorer (Chart.js)  │   CSV / PNG       │  1. analyses cache ── hit ─▶ return    │
│ EventsTable, DataPanel   │                   │  2. weather_series cache ─ miss ─┐     │
└──────────────────────────┘                   │  3. read from DB, clean, detect, │     │
                                               │     trends, insights             │     │
                                               │  4. store analysis, return       │     │
                                               └──────────────┬───────────────────┼─────┘
                                                              │                   │
                                                     MongoDB Atlas         Open-Meteo archive
                                                  (weather_series,         (ERA5, one call for
                                                   analyses, geocode)       all grid points)
                                                                           Nominatim (places)
```

### Backend layout (`backend/app`)

| File | Responsibility |
|---|---|
| `main.py` | Routes, CORS, security headers, rate-limit dependency, error handlers |
| `models.py` | `AnalysisParams`: every user input is validated here; cache-key hashing |
| `regions.py` | Snaps coordinates to the 0.25° ERA5 grid; samples up to 3×3 points in a box |
| `sources/open_meteo.py` | Safe upstream client: timeouts, retries with backoff, size cap, shape checks |
| `sources/geocode.py` | Nominatim search, throttled to 1 request/s, input sanitised |
| `storage.py` | MongoDB store (and an in-memory twin for dev/tests), TTL indexes |
| `cleaning.py` | Quality control: units, calendar gaps, impossible values, spikes, short-gap interpolation, coverage |
| `hazards/heatwave.py` | Threshold, event detection, yearly metrics |
| `trends.py` | Rolling mean, OLS, Mann-Kendall, Sen's slope |
| `insights.py` | Turns statistics into honest sentences and caveats |
| `service.py` | Orchestration, caching, single-flight locking |
| `exports.py` | CSV (formula-injection safe), PNG chart, PDF report |

### API

All endpoints are `GET`, so browsers and CDNs can cache them.

| Endpoint | Returns |
|---|---|
| `/api/health` | status, database reachability, last complete year |
| `/api/geocode?q=` | up to 6 places with centre point and bounding box |
| `/api/analyze?<region>&start_year&end_year&...` | full analysis JSON |
| `/api/series?<same>&year=` | daily Tmax, threshold and heatwave flag for one year |
| `/api/plot.png?<same>&metric=` | server-rendered trend chart |
| `/api/export.csv?<same>&table=yearly\|events` | CSV download |
| `/api/export.pdf?<same>` | PDF report |

`<region>` is either `lat` + `lon` or `south` + `west` + `north` + `east`.
Optional: `percentile` (80–99.5, default 95), `min_days` (2–10, default 3),
`threshold_method` (`fixed` | `calendar_day`), `baseline_start`/`baseline_end`
(default 1991–2020), `region_name` (display only).

### MongoDB collections

| Collection | `_id` | Contents | Expiry |
|---|---|---|---|
| `weather_series` | `era5:17.50:78.50` | Full daily Tmax record 1950→last year as packed float32 bytes (~110 KB), units, elevation, fetch time | 90 days |
| `analyses` | SHA-256 of normalised parameters + algorithm version | Finished analysis JSON | 7 days |
| `geocode` | `geo:<normalised query>` | Place search results | 30 days |

Weather is cached **per grid point and for the whole record**, so a different
year range, percentile or overlapping box reuses the download. Keys are built
by the server from validated numbers or hashes; no user text becomes a query.

---

## Data source

**Open-Meteo Historical Weather API**, model `era5`: ECMWF's ERA5 reanalysis,
0.25° grid (~28 km), from 1950 to the most recent complete year. Variable:
`temperature_2m_max`, daily, in the location's local time zone (`timezone=auto`).

Why ERA5 through Open-Meteo:

- Global, continuous and consistent back to 1950, so trends are not broken by
  station moves or instrument changes, which is the main hazard of station data.
- No API key, and several grid points can be fetched in one request.
- Stations (NOAA GHCN-Daily) are better for a specific town but have gaps and
  uneven coverage, especially in India. The cleaning code is written for that
  kind of data so the source can be swapped without changing the pipeline.

Place search uses **OpenStreetMap Nominatim**, which returns a bounding box for
each result. That box is what makes "select a state" work.

---

## Hazard logic

**Heatwave:** a run of at least **3 consecutive days** on which the daily
maximum temperature is **strictly above the 95th percentile** of daily maximum
temperature for that region. Both numbers can be changed in the UI.

Decisions the brief left open:

| Question | Choice | Why |
|---|---|---|
| Percentile of which period? | A fixed **baseline, 1991–2020** (current WMO normal), configurable | If the threshold came from the selected range, changing the range would move the threshold, and results could not be compared. |
| One threshold or seasonal? | **One for the whole year** by default; a **per-calendar-day** threshold (15-day window, as in the ETCCDI/HWMId family) is an option | The fixed version matches the brief and the everyday meaning of a heatwave (hot season only). The seasonal one finds "unusually hot for the time of year", including winter warm spells. |
| Missing days inside a run? | **Break the run** | We never assume an unknown day was hot. |
| Event crossing 31 Dec? | Counted in the **year it starts**; its days counted in the year they fall in | Avoids double-counting. |
| Events at the edge of the range? | Detection runs on the **full record**, then filters | An event that started the day before the range is not cut into a shorter fake event. |
| Box regions? | **Area-weighted mean** (cos latitude) of up to 3×3 grid points, then detect | A regional heatwave means the region as a whole is unusually hot. Caveat shown in the UI. |

Per event: start, end, duration, peak Tmax, mean excess over threshold, and
**cumulative excess** (°C·days above the threshold), used as intensity.

Per year: number of events, heatwave days, longest event, cumulative excess,
mean peak Tmax, and data coverage.

### Data cleaning (`cleaning.py`)

1. Convert °F or K to °C using the units the source reports.
2. Re-index onto a complete daily calendar; skipped dates become explicit gaps.
3. Remove impossible values (below −90 °C or above 60 °C).
4. Remove one-day spikes: a day more than 12 °C away from **both** neighbours
   in the same direction and from the 7-day median. A cold front is a step,
   not a spike, so it survives. **Real heat extremes are never clipped**: that
   would delete the signal being measured.
5. Interpolate gaps of 1–2 days only. Longer gaps stay missing. (Pandas'
   `interpolate(limit=2)` would also partly fill longer gaps, so the code
   measures gap lengths first.)
6. Years with under 90% valid days are shown as "no data" and left out of
   trend tests, rather than counted as zero heatwaves.

The data panel in the UI reports how many values each step touched.

### Trend analysis (`trends.py`)

- **Rolling mean** (10 years for ranges of 30+ years, otherwise 5): for the chart only.
- **OLS regression**: slope, p-value, R². Familiar, but it assumes normal residuals,
  which small yearly counts with many zeros are not.
- **Mann-Kendall test + Sen's slope**: non-parametric and robust to outliers; the
  usual choice for climate extremes. The headline uses these.

### How insights are worded (`insights.py`)

- A trend is called an increase or decrease only if Mann-Kendall p < 0.05.
  Otherwise the sentence says there is **no statistically significant trend**,
  even if the line slopes.
- Percent change comes from the trend line (start vs. end), not from the first
  and last single years, which are noisy.
- No percentage is given when the starting level is under 1 per year, because
  0.3 → 0.9 events is "+200%" and means little.
- Caveats are generated from the data: short ranges, rare events, gaps, box
  averaging, the baseline, reanalysis vs. stations, multiple testing, and
  trend ≠ attribution.

---

## Security and performance

- **Input validation:** Pydantic bounds on every parameter (lat/lon ranges, box
  ≤ 10° per side, years within the record, ≥ 10-year ranges, percentile 80–99.5).
  Bad input gets a 422 with readable messages.
- **Rate limiting:** token bucket per client IP; 60/min for light endpoints,
  12/min for analysis and exports; 429 with `Retry-After`. Behind Render's proxy
  the client IP is read from the right-hand end of `X-Forwarded-For`
  (`TRUSTED_PROXY_HOPS=1`), because left-hand entries can be forged.
- **Safe upstream access:** fixed base URLs; only numbers and enums are sent
  upstream; timeouts; 25 MB response cap; retries with exponential backoff and
  jitter on 429/5xx, respecting `Retry-After`; a semaphore caps concurrent
  upstream calls; Nominatim is limited to 1 request/s per its usage policy.
- **Caching:** three layers (MongoDB weather per grid point, MongoDB analyses,
  HTTP `Cache-Control`), plus a small in-process LRU of cleaned series. A
  per-key lock means simultaneous requests for the same new region trigger one
  download, not many.
- **Other:** CORS restricted to configured origins and GET only; security
  headers (nosniff, frame deny, CSP for the API); stack traces logged, never
  returned; CSV cells that start with `= + - @` are escaped; display names are
  stripped of control characters; pandas work runs in a worker thread so the
  event loop stays responsive.

---

## Setup

Requirements: Python 3.11+, Node 18+, optionally a MongoDB Atlas free cluster.

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # set MONGO_URI; leave empty for the in-memory store
uvicorn app.main:app --reload --port 8000
pytest                      # 46 tests, run offline against fake data providers
```

### Frontend

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173, /api is proxied to :8000
```

### Deploy

1. **MongoDB Atlas:** create a free M0 cluster and a database user, allow
   access from anywhere (Render's free tier has no fixed IP), copy the
   connection string.
2. **Render:** New → Web Service → this repo (or New → Blueprint, which reads
   `render.yaml`). Settings:
   - Root Directory `backend`
   - Build Command `pip install -r requirements.txt`
   - Start Command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Environment: `MONGO_URI`, `CORS_ORIGINS` (your Vercel URL, comma-separated
     with any others), `NOMINATIM_USER_AGENT`, `TRUSTED_PROXY_HOPS=1`,
     `PYTHON_VERSION=3.12.7`
3. **Vercel:** import the repo, Root Directory `frontend`, framework Vite, set
   `VITE_API_URL` to the Render URL (no trailing slash).
4. Put the Vercel URL into Render's `CORS_ORIGINS` and redeploy.

Render's free tier sleeps when idle; the first request after a pause takes
30–60 s, and the UI says so.

---

## Assumptions

- "Region" means one ERA5 grid cell for a point, or the area-average of a
  sample of cells for a box or a state's bounding box. A state's bounding box
  includes some land outside the state.
- Boxes are limited to 10° per side and sampled at up to 3×3 points to keep
  upstream usage reasonable. Boxes that cross the antimeridian are not supported.
- Only complete calendar years are analysed (up to last year).
- The heatwave definition uses daily **maximum** temperature only. Minimum
  temperature (hot nights) and humidity matter for health but are out of scope.
- ERA5 is used as delivered; it has no gaps, so cleaning mostly matters if the
  source changes. The tests inject gaps, spikes and impossible values to prove
  the cleaning works.

## Limitations and what I would improve with more time

- **Serial correlation:** Mann-Kendall assumes independent years. Add the
  Hamed-Rao or pre-whitening correction.
- **Field significance:** testing several metrics (and many regions) inflates
  false positives; add an FDR correction or report it more formally.
- **More hazards:** drought (SPI from precipitation) and heavy rainfall (annual
  count of days above the 99th percentile of wet-day rainfall, Rx1day) would be
  new modules beside `hazards/heatwave.py` with the same interface.
- **Real boundaries:** use state polygons and mask grid cells, instead of
  bounding boxes.
- **Station comparison:** overlay NOAA GHCN-Daily stations where they exist.
- **Shared rate limits:** move buckets to Redis so limits hold across instances.
- **Map layer:** a choropleth of trend per grid cell across a box.
- **Frontend tests** (Playwright) in CI, and a MongoDB integration test against
  a real database. The Mongo store is exercised through the same interface as
  the in-memory store, but the automated tests run without a database.

---

## AI tools used

**Claude (Anthropic)**, used through the Claude app, for most of the
implementation.

- **What I decided:** the stack (FastAPI, MongoDB Atlas, React with Vite,
  Chart.js and Leaflet, Render and Vercel), which bonus features to build
  (map region picker, Mann-Kendall and regression, CSV/PDF export), and the
  scope (heatwave first, done properly, rather than several hazards half-done).
- **What Claude did:** generated the first version of the backend (data
  ingestion and caching, cleaning, heatwave detection, trend statistics,
  insights, exports, rate limiting), the React frontend, the 46 backend
  tests, and the first draft of this README.
- **What I did:** ran the app locally against real Open-Meteo data and
  checked the results made sense; found and got fixed a usability bug (the
  results did not update when a new region was picked); set up MongoDB Atlas
  and confirmed the collections and caching work; deployed the backend to
  Render and the frontend to Vercel, debugging the failed first deploy (a
  missing Root Directory) and the CORS setup; and reviewed the code so I can
  explain and change any part of it.