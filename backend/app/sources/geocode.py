"""Place search (city / state / country) via OpenStreetMap Nominatim.

Nominatim returns a bounding box for every result, which is what lets a user
pick a whole state: we turn that box into a lat-lon box region.

Nominatim's usage policy allows at most 1 request per second and requires an
identifying User-Agent, so calls go through a global lock that spaces them
out, and every result is cached in the database.
"""
from __future__ import annotations

import asyncio
import re
import time

import httpx

from .open_meteo import UpstreamError

_QUERY_RE = re.compile(r"[^\w\s,.\-']", re.UNICODE)


def normalise_query(q: str) -> str:
    """Strip anything but letters, digits, spaces and basic punctuation."""
    q = _QUERY_RE.sub(" ", q)
    return re.sub(r"\s+", " ", q).strip().lower()[:100]


class Geocoder:
    def __init__(self, url: str, user_agent: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url = url
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0), transport=transport,
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
        )
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def search(self, query: str) -> list[dict]:
        async with self._lock:  # one request at a time, >= 1 s apart
            wait = 1.0 - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await self._client.get(self.url, params={
                    "q": query, "format": "jsonv2", "limit": 6, "addressdetails": 0,
                })
            except httpx.HTTPError as exc:
                raise UpstreamError("Place search is unavailable right now.") from exc
            finally:
                self._last_call = time.monotonic()
        if resp.status_code != 200:
            raise UpstreamError("Place search is unavailable right now.")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise UpstreamError("Unexpected response from place search.") from exc
        return [r for r in (self._to_place(item) for item in raw[:6]) if r]

    @staticmethod
    def _to_place(item: dict) -> dict | None:
        try:
            south, north, west, east = (float(v) for v in item["boundingbox"])
            return {
                "name": str(item.get("display_name", ""))[:200],
                "kind": str(item.get("addresstype") or item.get("type") or "place")[:40],
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "bbox": {"south": south, "west": west, "north": north, "east": east},
            }
        except (KeyError, TypeError, ValueError):
            return None
