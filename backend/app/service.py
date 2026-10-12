"""Orchestration: region -> cached weather -> cleaned series -> hazards -> trends.

Request flow for /api/analyze:

  1. Look up the finished analysis by parameter hash in `analyses`. Hit -> done.
  2. Otherwise make sure every needed grid point's full daily record is in
     `weather_series`; fetch only the missing points from Open-Meteo (one
     multi-location call) and store them.
  3. Read the series back from the database, clean, detect, analyse.
  4. Store the result in `analyses` and return it.

Concurrency: if ten users ask for the same uncached region at once, a
per-key asyncio lock makes one request do the upstream fetch while the
others wait and then read from the cache ("single flight"). CPU-heavy
pandas work runs in a worker thread so the event loop stays responsive.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import date, datetime, timezone

import pandas as pd

from .cleaning import QualityReport, area_mean, clean_tmax
from .config import Settings
from .hazards.heatwave import METRICS, compute_threshold, detect_events, yearly_metrics
from .insights import build_insights
from .models import ANALYSIS_VERSION, AnalysisParams
from .regions import GridPoint, area_weights, grid_points_for_box, grid_points_for_point
from .sources.open_meteo import OpenMeteoClient
from .storage import Store, pack, unpack
from .trends import analyse_series

log = logging.getLogger(__name__)


class KeyedLocks:
    """One asyncio.Lock per key, created on demand."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            if len(self._locks) > 1000:  # drop idle locks so the dict can't grow forever
                self._locks = {k: v for k, v in self._locks.items() if v.locked()}
            self._locks[key] = asyncio.Lock()
        return self._locks[key]


class AnalysisService:
    def __init__(self, settings: Settings, store: Store, client: OpenMeteoClient):
        self.settings = settings
        self.store = store
        self.client = client
        self._locks = KeyedLocks()
        self._series_cache: OrderedDict[str, tuple] = OrderedDict()  # small in-process LRU

    # ------------------------------------------------------------------ data
    def points_for(self, p: AnalysisParams) -> list[GridPoint]:
        if p.is_box:
            return grid_points_for_box(p.south, p.west, p.north, p.east,
                                       self.settings.max_grid_points_per_axis)
        return grid_points_for_point(p.lat, p.lon)

    def _doc_id(self, pt: GridPoint) -> str:
        return f"{self.client.model}:{pt.key}"

    def _record_end(self) -> date:
        return date(self.settings.last_complete_year, 12, 31)

    async def ensure_weather(self, points: list[GridPoint]) -> dict:
        """Make sure every point's record is cached; returns cache stats."""
        end = self._record_end()

        async def missing() -> list[GridPoint]:
            out = []
            for pt in points:
                doc = await self.store.get_weather(self._doc_id(pt))
                if doc is None or doc["end"] < end.isoformat():
                    out.append(pt)
            return out

        todo = await missing()
        if not todo:
            return {"weather_cache": "hit", "fetched_points": 0}
        lock_key = "fetch:" + "|".join(sorted(self._doc_id(p) for p in todo))
        async with self._locks.get(lock_key):
            todo = await missing()  # someone may have fetched while we waited
            if not todo:
                return {"weather_cache": "hit", "fetched_points": 0}
            log.info("Fetching %d grid point(s) from Open-Meteo", len(todo))
            start = self.settings.data_start
            for raw in await self.client.fetch_tmax(todo, start, end):
                await self.store.put_weather({
                    "_id": self._doc_id(raw.point),
                    "lat": raw.point.lat, "lon": raw.point.lon,
                    "model": self.client.model,
                    "start": start.isoformat(), "end": end.isoformat(),
                    "units": raw.units,
                    "elevation": raw.elevation,
                    "source_lat": raw.source_lat, "source_lon": raw.source_lon,
                    "tmax": pack(raw.values),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })
        return {"weather_cache": "miss", "fetched_points": len(todo)}

    async def load_clean_series(self, points: list[GridPoint]):
        """Read point records from the database, clean them, and area-average."""
        cache_key = "|".join(p.key for p in points)
        if cache_key in self._series_cache:
            self._series_cache.move_to_end(cache_key)
            return self._series_cache[cache_key]

        docs = [await self.store.get_weather(self._doc_id(p)) for p in points]
        if any(d is None for d in docs):
            raise RuntimeError("Weather record disappeared from the cache; please retry.")

        def work():
            cleaned, reports = [], []
            for d in docs:
                values = unpack(d["tmax"])
                idx = pd.date_range(d["start"], periods=len(values), freq="D")
                s, rep = clean_tmax(pd.Series(values, index=idx), d["units"])
                cleaned.append(s)
                reports.append(rep)
            series = cleaned[0] if len(cleaned) == 1 else area_mean(cleaned, area_weights(points))
            quality = _merge_reports(reports, series)
            meta = [{"lat": d["lat"], "lon": d["lon"], "elevation": d.get("elevation")} for d in docs]
            return series, quality, meta

        result = await asyncio.to_thread(work)
        self._series_cache[cache_key] = result
        if len(self._series_cache) > 16:
            self._series_cache.popitem(last=False)
        return result

    # -------------------------------------------------------------- analysis
    async def analyze(self, p: AnalysisParams) -> dict:
        key = p.cache_key()
        cached = await self.store.get_analysis(key)
        if cached:
            return {**cached, "cache": {"analysis_cache": "hit"}}
        async with self._locks.get("analysis:" + key):
            cached = await self.store.get_analysis(key)
            if cached:
                return {**cached, "cache": {"analysis_cache": "hit"}}
            points = self.points_for(p)
            fetch_info = await self.ensure_weather(points)
            series, quality, point_meta = await self.load_clean_series(points)
            result = await asyncio.to_thread(self._compute, p, series, quality, point_meta)
            await self.store.put_analysis(key, p.model_dump(mode="json"), result)
            return {**result, "cache": {"analysis_cache": "miss", **fetch_info}}

    def _region_label(self, p: AnalysisParams) -> str:
        if p.region_name:
            return p.region_name
        if p.is_box:
            return f"the box {p.south:.2f}..{p.north:.2f}°N, {p.west:.2f}..{p.east:.2f}°E"
        return f"{p.lat:.2f}°N, {p.lon:.2f}°E"

    def _threshold(self, p: AnalysisParams, series: pd.Series) -> tuple[pd.Series, dict]:
        thr = compute_threshold(series, (p.baseline_start, p.baseline_end), p.percentile,
                                p.threshold_method.value)
        if p.threshold_method.value == "fixed":
            info = {"value": round(float(thr.iloc[0]), 2),
                    "description": f"{p.percentile:g}th percentile of daily Tmax = {thr.iloc[0]:.1f} °C"}
        else:
            info = {"min": round(float(thr.min()), 2), "max": round(float(thr.max()), 2),
                    "description": f"{p.percentile:g}th percentile of daily Tmax for each calendar day "
                                   f"(±7-day window), {thr.min():.1f}-{thr.max():.1f} °C"}
        return thr, {"method": p.threshold_method.value, "percentile": p.percentile,
                     "baseline": [p.baseline_start, p.baseline_end], **info}

    def _compute(self, p: AnalysisParams, series: pd.Series, quality: dict, point_meta: list) -> dict:
        thr, thr_info = self._threshold(p, series)
        all_events = detect_events(series, thr, p.min_days)  # whole record, so range edges are handled
        years = range(p.start_year, p.end_year + 1)
        events = [e.as_dict() for e in all_events if p.start_year <= e.start.year <= p.end_year]
        yearly = yearly_metrics(series, thr, all_events, years, set(quality["incomplete_years"]))

        window = 10 if len(years) >= 30 else 5
        trends = {m: analyse_series([r["year"] for r in yearly], [r[m] for r in yearly], window)
                  for m in METRICS}
        region = self._region_label(p)
        insights = build_insights(region=region, params=p, yearly=yearly, trends=trends,
                                  events=events, quality=quality, threshold=thr_info,
                                  is_box=p.is_box, n_points=len(point_meta))
        return {
            "meta": {
                "region": {
                    "label": region,
                    "type": "box" if p.is_box else "point",
                    "query": p.model_dump(mode="json", include={"lat", "lon", "south", "west", "north", "east"}),
                    "grid_points": point_meta,
                },
                "hazard": p.hazard.value,
                "start_year": p.start_year, "end_year": p.end_year,
                "min_days": p.min_days,
                "data_source": {
                    "name": "Open-Meteo Historical Weather API",
                    "dataset": f"ECMWF {self.client.model.upper()} reanalysis",
                    "resolution": "0.25° (~28 km)",
                    "variable": "temperature_2m_max (daily, local time)",
                    "record": f"{self.settings.data_start.year}-{self.settings.last_complete_year}",
                },
                "analysis_version": ANALYSIS_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            "threshold": thr_info,
            "quality": quality,
            "metrics": METRICS,
            "yearly": yearly,
            "trends": trends,
            "events": events,
            "insights": insights,
        }

    async def daily_series(self, p: AnalysisParams, year: int) -> dict:
        """Daily Tmax, threshold and heatwave flags for one year (event explorer)."""
        points = self.points_for(p)
        await self.ensure_weather(points)
        series, _, _ = await self.load_clean_series(points)

        def work():
            thr, info = self._threshold(p, series)
            events = detect_events(series, thr, p.min_days)
            mask = series.index.year == year
            in_hw = pd.Series(False, index=series.index)
            for e in events:
                in_hw[e.start:e.end] = True
            s, t, h = series[mask], thr[mask], in_hw[mask]
            return {
                "year": year, "threshold": info,
                "dates": [d.date().isoformat() for d in s.index],
                "tmax": [None if pd.isna(v) else round(float(v), 2) for v in s],
                "threshold_values": [round(float(v), 2) for v in t],
                "heatwave": [bool(v) for v in h],
            }
        return await asyncio.to_thread(work)


def _merge_reports(reports: list[QualityReport], series: pd.Series) -> dict:
    """Sum QC counts over grid points; coverage is judged on the final series."""
    keys = ["missing_in_source", "out_of_range_removed", "spikes_removed", "interpolated"]
    merged = {k: int(sum(getattr(r, k) for r in reports)) for k in keys}
    merged["total_days"] = int(reports[0].total_days)
    merged["points"] = len(reports)
    merged["still_missing"] = int(series.isna().sum())
    conv = {r.units_converted_from for r in reports if r.units_converted_from}
    merged["units_converted_from"] = sorted(conv)
    cov = series.notna().groupby(series.index.year).mean()
    merged["incomplete_years"] = [int(y) for y, c in cov.items() if c < 0.9]
    return merged
