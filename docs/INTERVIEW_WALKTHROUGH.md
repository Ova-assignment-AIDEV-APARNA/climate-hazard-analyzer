# Interview walkthrough (keep this out of the public repo if you prefer)

Read this with the code open. The goal: you can explain every file and make
likely changes live without hunting.

## 1. Follow one request end to end

User clicks **Analyse heatwaves** in the UI:

1. `frontend/src/App.jsx` → `run()` builds params with `analysisParams()` from
   `src/api.js` and calls `getJSON('/api/analyze', params, signal)`. An
   `AbortController` cancels an older request if a newer one starts.
2. `backend/app/main.py` → route `analyze`:
   - `Depends(limit(heavy))` → `ratelimit.py` token bucket. Empty → 429.
   - `Depends(analysis_params_dep)` → `models.py` `AnalysisParams`. Invalid →
     Pydantic `ValidationError` → handler returns 422 with readable messages.
3. `service.py` → `AnalysisService.analyze(p)`:
   - `p.cache_key()` = SHA-256 of the normalised params + `ANALYSIS_VERSION`.
   - `store.get_analysis(key)` → hit? return immediately.
   - Lock on the key (single flight), check again, then:
   - `points_for(p)` → `regions.py` snaps to the 0.25° grid (box → ≤ 3×3 points).
   - `ensure_weather(points)` → for each point, is `era5:<lat>:<lon>` in
     `weather_series` and up to date? Missing ones are fetched in **one**
     multi-location call (`sources/open_meteo.py`) and stored.
   - `load_clean_series(points)` → reads the docs **from the database**,
     unpacks float32 bytes, runs `cleaning.clean_tmax` per point, area-averages.
   - `_compute(...)` in a worker thread: threshold → events on the **full
     record** → filter to range → `yearly_metrics` → `analyse_series` per
     metric → `build_insights`.
   - `store.put_analysis(...)`, return JSON.
4. Back in React: `Headline`, `StatTiles`, `TrendChart`, `YearExplorer`
   (calls `/api/series` for the chosen year), `Details`, `EventsTable`, `DataPanel`.

## 2. One-line answers to likely questions

- **Why the 1991–2020 baseline and not the selected range?** So the threshold
  doesn't move when the user changes the range; results stay comparable.
- **Why Mann-Kendall over linear regression?** Yearly counts are small integers
  with many zeros, not normal; MK is rank-based and robust. OLS is still shown.
- **Why Sen's slope for the percentage?** It's the median of pairwise slopes,
  so one extreme year can't drag it. Percent compares trend-line start vs end.
- **Why not clip extreme temperatures as outliers?** Heatwaves *are* the
  extremes. Only impossible values and one-day up-and-down spikes are removed.
- **Why break runs on missing days?** Inventing hot days would inflate counts.
- **Why cache per grid point instead of per request?** A different year range
  or percentile reuses the same download; overlapping boxes share points.
- **Why float32 bytes in Mongo?** ~27k numbers → ~110 KB vs. ~400 KB as a BSON
  array; well under the 16 MB document limit. Precision of 0.1 °C is plenty.
- **Why GET for everything?** Idempotent, cacheable by browsers/CDNs, and
  links are shareable.
- **How does rate limiting work behind a proxy?** Render's proxy is the TCP
  peer, so we read `X-Forwarded-For`; the right-hand entry is the one the
  proxy added (left ones can be forged). Buckets are in-process; Redis would
  share them across instances.
- **What if Open-Meteo is down?** Retries with backoff, then a 502 with a clear
  message. Cached regions keep working.
- **Is the trend caused by climate change?** The app doesn't claim that; it
  says so in the caveats.
- **Weakness you'd fix first?** Serial correlation in MK (Hamed-Rao), then
  multiple-testing correction.

## 3. Likely live changes and exactly where to make them

| Ask | Where | What to change |
|---|---|---|
| Default percentile 95 → 90 | `models.py` (`percentile` default and the `Query` default), `App.jsx` `DEFAULT_SETTINGS` | change the numbers |
| Heatwave needs ≥ 5 days | same pattern with `min_days` | |
| Use a 90th percentile *and* require Tmax ≥ 40 °C | `hazards/heatwave.py` `detect_events`: `hot = ((s > threshold) & (s >= 40)).to_numpy()` | bump `ANALYSIS_VERSION` so old cache isn't served |
| Add a new metric, e.g. "first heatwave day of year" | `yearly_metrics` (add the field), `METRICS` dict (label), `insights.UNITS/NOUN` if used in text, `TrendChart.jsx` `METRIC_TABS` + `UNITS` | trends are computed automatically for every key in `METRICS` |
| Change rolling window | `service._compute`: `window = 10 if len(years) >= 30 else 5` | |
| Make significance level 0.1 | `trends.mann_kendall` (`p < 0.05`) and `insights.SIGNIFICANCE` | |
| Cache analyses for 1 day | `.env` `ANALYSIS_CACHE_DAYS=1` (config.py) | Mongo TTL index is created at startup; existing index must be dropped to change |
| Tighter rate limit | `.env` `HEAVY_RATE_LIMIT_PER_MINUTE` | |
| Allow bigger boxes | `config.max_bbox_span_deg` and `MAX_SPAN` in `RegionPicker.jsx` | |
| Add an endpoint returning just the headline | `main.py`: new `@app.get` that calls `service.analyze(p)` and returns `result["insights"]["headline"]` | add `dependencies=[Depends(limit(heavy))]` |
| Add drought hazard | new `hazards/drought.py` with `compute_threshold`/`detect_events`/`yearly_metrics`; fetch `precipitation_sum` in `open_meteo.py`; branch on `p.hazard` in `service._compute`; enable the option in `SettingsPanel.jsx` | the store keys would need the variable name |
| Show OLS instead of Sen line on the chart | `TrendChart.jsx` third dataset: use `trend.ols.intercept/slope_per_year` | |

After any logic change: `pytest` in `backend/` (46 tests, ~4 s).

## 4. Things to verify yourself before submitting

1. Run locally against real data (internet needed): Hyderabad 1990–2020, a
   state (e.g. Telangana → "Use whole area"), and a drawn box. Read the
   headline and check it makes sense against the charts.
2. Check that Open-Meteo accepts `models=era5` and multi-location requests
   (`latitude=17.5,18.0`), and how long a fresh fetch takes.
3. Connect MongoDB Atlas, run once, and confirm the three collections and the
   TTL indexes appear; `/api/health` should show `"database": true`.
4. After deploying to Render, check the logs for the client IP your rate
   limiter sees; adjust `TRUSTED_PROXY_HOPS` if it is the proxy's address.
5. Fill in the "AI tools used" section of the README honestly.
6. Add a screenshot of the deployed app.
