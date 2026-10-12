"""Client for the Open-Meteo historical weather archive (ERA5 reanalysis).

Safe external access:
  * fixed base URL from config; we only ever add numeric/enum params
  * timeouts on every request, and a cap on response size
  * a global semaphore so we never run more than N upstream calls at once
  * retries with exponential backoff + jitter on 429 / 5xx / network errors,
    honouring Retry-After when the server sends one
  * the response shape and units are checked before anything is stored
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import date

import httpx
import numpy as np

from ..regions import GridPoint

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class UpstreamError(Exception):
    """The data provider failed or returned something unusable."""


@dataclass
class RawSeries:
    point: GridPoint
    start: date
    values: np.ndarray  # daily Tmax as floats, NaN where the source had null
    units: str
    elevation: float | None
    source_lat: float | None
    source_lon: float | None


class OpenMeteoClient:
    def __init__(self, base_url: str, model: str, timeout_s: float,
                 max_retries: int, concurrency: int,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self.model = model
        self.max_retries = max_retries
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            transport=transport,
            headers={"User-Agent": "climate-hazard-analyzer/1.0"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_tmax(self, points: list[GridPoint], start: date, end: date) -> list[RawSeries]:
        """Fetch daily maximum 2 m temperature for several grid points in one call."""
        params = {
            "latitude": ",".join(f"{p.lat:.2f}" for p in points),
            "longitude": ",".join(f"{p.lon:.2f}" for p in points),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "temperature_2m_max",
            "temperature_unit": "celsius",
            "timezone": "auto",  # daily max over the *local* calendar day
            "models": self.model,
            "cell_selection": "nearest",
        }
        payload = await self._get_json(params)
        items = payload if isinstance(payload, list) else [payload]
        if len(items) != len(points):
            raise UpstreamError("Provider returned a different number of locations than requested.")
        expected_days = (end - start).days + 1
        return [self._parse(item, p, start, expected_days) for item, p in zip(items, points)]

    async def _get_json(self, params: dict):
        last_error = "unknown error"
        async with self._sem:
            for attempt in range(self.max_retries + 1):
                try:
                    async with self._client.stream("GET", self.base_url, params=params) as resp:
                        body = await self._read_capped(resp)
                        if resp.status_code == 200:
                            return json.loads(body)
                        if resp.status_code == 429 or resp.status_code >= 500:
                            last_error = f"HTTP {resp.status_code}"
                            await self._backoff(attempt, resp.headers.get("Retry-After"))
                            continue
                        # 4xx other than 429: our request is wrong; retrying won't help.
                        raise UpstreamError(self._reason(body, resp.status_code))
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = type(exc).__name__
                    log.warning("Open-Meteo attempt %d failed: %s", attempt + 1, last_error)
                    await self._backoff(attempt, None)
        raise UpstreamError(f"Weather data provider unavailable after retries ({last_error}).")

    async def _read_capped(self, resp: httpx.Response) -> bytes:
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise UpstreamError("Provider response too large.")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self.max_retries:
            return
        delay = 2 ** attempt + random.uniform(0, 1)
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        await asyncio.sleep(min(delay, 30.0))

    @staticmethod
    def _reason(body: bytes, status: int) -> str:
        try:
            reason = json.loads(body).get("reason")
            if reason:
                return f"Provider rejected the request: {str(reason)[:200]}"
        except Exception:
            pass
        return f"Provider rejected the request (HTTP {status})."

    @staticmethod
    def _parse(item: dict, point: GridPoint, start: date, expected_days: int) -> RawSeries:
        try:
            daily = item["daily"]
            times = daily["time"]
            raw = daily["temperature_2m_max"]
            units = item.get("daily_units", {}).get("temperature_2m_max", "°C")
        except (KeyError, TypeError) as exc:
            raise UpstreamError("Unexpected response format from provider.") from exc
        if len(times) != len(raw):
            raise UpstreamError("Provider returned mismatched time and value arrays.")
        if not times or times[0] != start.isoformat():
            raise UpstreamError("Provider returned a different start date than requested.")
        if len(times) != expected_days:
            # Not fatal: cleaning re-indexes onto the full calendar, but log it.
            log.warning("Expected %d days, got %d for %s", expected_days, len(times), point.key)
        values = np.array([np.nan if v is None else float(v) for v in raw], dtype=float)
        # Re-index by date in case the provider skipped days.
        if len(times) != expected_days:
            full = np.full(expected_days, np.nan)
            for t, v in zip(times, values):
                idx = (date.fromisoformat(t) - start).days
                if 0 <= idx < expected_days:
                    full[idx] = v
            values = full
        return RawSeries(
            point=point, start=start, values=values, units=units,
            elevation=item.get("elevation"),
            source_lat=item.get("latitude"), source_lon=item.get("longitude"),
        )
