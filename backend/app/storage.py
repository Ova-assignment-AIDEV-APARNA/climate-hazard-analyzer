"""Persistence layer (MongoDB, with an in-memory stand-in for dev/tests).

Three collections:

  weather_series  one document per ERA5 grid point: the full daily Tmax
                  record (1950 -> last complete year) packed as float32
                  bytes. ~27k days -> ~110 KB per document, far below
                  MongoDB's 16 MB document limit.
  analyses        finished analysis results keyed by a hash of the
                  request parameters. Expire after a few days (TTL index).
  geocode         cached place-search results, so we respect Nominatim's
                  1 request/second policy.

Every key is built by our own code from validated numbers or a hash, so no
user-supplied string is ever used as a query operator (no NoSQL injection).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

import numpy as np


def pack(values: np.ndarray) -> bytes:
    return np.asarray(values, dtype="<f4").tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4").astype(float)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store(Protocol):
    async def get_weather(self, doc_id: str) -> Optional[dict]: ...
    async def put_weather(self, doc: dict) -> None: ...
    async def get_analysis(self, key: str) -> Optional[dict]: ...
    async def put_analysis(self, key: str, params: dict, result: dict) -> None: ...
    async def get_geocode(self, key: str) -> Optional[list]: ...
    async def put_geocode(self, key: str, results: list) -> None: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class MemoryStore:
    """Dict-backed store with the same expiry behaviour as the Mongo TTLs."""

    def __init__(self, weather_days: int, analysis_days: int, geocode_days: int):
        self._ttl = {
            "weather": weather_days * 86400,
            "analysis": analysis_days * 86400,
            "geocode": geocode_days * 86400,
        }
        self._data: dict[str, dict[str, tuple[float, Any]]] = {k: {} for k in self._ttl}

    def _get(self, coll: str, key: str):
        hit = self._data[coll].get(key)
        if hit is None:
            return None
        stored_at, value = hit
        if time.time() - stored_at > self._ttl[coll]:
            del self._data[coll][key]
            return None
        return value

    def _put(self, coll: str, key: str, value: Any) -> None:
        self._data[coll][key] = (time.time(), value)

    async def get_weather(self, doc_id):
        return self._get("weather", doc_id)

    async def put_weather(self, doc):
        self._put("weather", doc["_id"], doc)

    async def get_analysis(self, key):
        doc = self._get("analysis", key)
        return doc["result"] if doc else None

    async def put_analysis(self, key, params, result):
        self._put("analysis", key, {"params": params, "result": result})

    async def get_geocode(self, key):
        return self._get("geocode", key)

    async def put_geocode(self, key, results):
        self._put("geocode", key, results)

    async def ping(self):
        return True

    async def close(self):
        pass


class MongoStore:
    def __init__(self, uri: str, db_name: str, weather_days: int,
                 analysis_days: int, geocode_days: int):
        # PyMongo's native asyncio client (Motor's successor).
        from pymongo import AsyncMongoClient

        self._client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        db = self._client[db_name]
        self.weather = db["weather_series"]
        self.analyses = db["analyses"]
        self.geocode = db["geocode"]
        self._ttl = (weather_days, analysis_days, geocode_days)

    async def init_indexes(self) -> None:
        w, a, g = self._ttl
        # MongoDB deletes documents automatically once created_at + TTL passes.
        await self.weather.create_index("created_at", expireAfterSeconds=w * 86400)
        await self.analyses.create_index("created_at", expireAfterSeconds=a * 86400)
        await self.geocode.create_index("created_at", expireAfterSeconds=g * 86400)

    async def get_weather(self, doc_id):
        return await self.weather.find_one({"_id": doc_id})

    async def put_weather(self, doc):
        doc = {**doc, "created_at": _now()}
        await self.weather.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_analysis(self, key):
        doc = await self.analyses.find_one({"_id": key})
        # The TTL monitor runs about once a minute; double-check expiry here.
        if doc and doc["created_at"] < _now() - timedelta(days=self._ttl[1]):
            return None
        return doc["result"] if doc else None

    async def put_analysis(self, key, params, result):
        await self.analyses.replace_one(
            {"_id": key},
            {"_id": key, "params": params, "result": result, "created_at": _now()},
            upsert=True,
        )

    async def get_geocode(self, key):
        doc = await self.geocode.find_one({"_id": key})
        return doc["results"] if doc else None

    async def put_geocode(self, key, results):
        await self.geocode.replace_one(
            {"_id": key}, {"_id": key, "results": results, "created_at": _now()}, upsert=True
        )

    async def ping(self):
        try:
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False

    async def close(self):
        await self._client.close()
