"""Shared fixtures: a fake Open-Meteo and a fake Nominatim.

The fakes return synthetic but realistic data (seasonal cycle, a warming
trend, random noise, plus deliberate nulls and spikes), so the full
pipeline can be tested offline and deterministically.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.sources.geocode import Geocoder
from app.sources.open_meteo import OpenMeteoClient
from app.storage import MemoryStore


def synthetic_tmax(start: str, end: str, lat: float, warming_per_decade: float = 0.4,
                   seed: int = 1) -> pd.Series:
    idx = pd.date_range(start, end, freq="D")
    rng = np.random.default_rng(seed + int(lat * 100))
    doy = idx.dayofyear.to_numpy()
    years = (idx.year - idx.year[0]).to_numpy() + doy / 365.25
    seasonal = 32 + 7 * np.sin(2 * np.pi * (doy - 80) / 365.25)
    # AR(1) noise so hot days cluster into multi-day spells, as in reality
    noise = np.zeros(len(idx))
    eps = rng.normal(0, 1.6, len(idx))
    for i in range(1, len(idx)):
        noise[i] = 0.7 * noise[i - 1] + eps[i]
    return pd.Series(seasonal + warming_per_decade * years / 10 + noise, index=idx)


class FakeOpenMeteo:
    def __init__(self):
        self.calls = 0
        self.fail_first = 0  # number of 429s to return before succeeding

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        q = parse_qs(request.url.query.decode())
        lats = [float(v) for v in q["latitude"][0].split(",")]
        lons = [float(v) for v in q["longitude"][0].split(",")]
        start, end = q["start_date"][0], q["end_date"][0]
        items = []
        for la, lo in zip(lats, lons):
            s = synthetic_tmax(start, end, la)
            vals = [round(float(v), 1) for v in s]
            if len(vals) > 9000:  # inject faults only into full-length records
                vals[100] = None              # a one-day gap
                vals[5000:5006] = [None] * 6  # a six-day gap
                vals[8000] = 85.0             # an impossible value
            items.append({
                "latitude": la, "longitude": lo, "elevation": 500.0,
                "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
                "daily": {"time": [d.date().isoformat() for d in s.index],
                          "temperature_2m_max": vals},
            })
        return httpx.Response(200, json=items if len(items) > 1 else items[0])


class FakeNominatim:
    def __init__(self):
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json=[{
            "display_name": "Hyderabad, Telangana, India", "addresstype": "city",
            "lat": "17.385", "lon": "78.4867",
            "boundingbox": ["17.2", "17.6", "78.2", "78.7"],
        }])


@pytest.fixture
def settings():
    return Settings(mongo_uri="", cors_origins="http://localhost:5173",
                    rate_limit_per_minute=1000, heavy_rate_limit_per_minute=1000,
                    data_start=date(1950, 1, 1))


@pytest.fixture
def fake_meteo():
    return FakeOpenMeteo()


@pytest.fixture
def fake_geo():
    return FakeNominatim()


@pytest.fixture
def client(settings, fake_meteo, fake_geo):
    om = OpenMeteoClient(settings.open_meteo_url, "era5", 10, 2, 2,
                         transport=httpx.MockTransport(fake_meteo.handler))
    geo = Geocoder(settings.nominatim_url, "test", transport=httpx.MockTransport(fake_geo.handler))
    store = MemoryStore(1, 1, 1)
    app = create_app(settings, store=store, client=om, geocoder=geo)
    with TestClient(app) as c:
        yield c
