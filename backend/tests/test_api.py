"""End-to-end API tests against the fake data providers."""
import asyncio
from datetime import date

import httpx
import pytest

from app.regions import GridPoint
from app.sources.open_meteo import OpenMeteoClient, UpstreamError

POINT = {"lat": 17.385, "lon": 78.4867, "start_year": 1990, "end_year": 2020,
         "region_name": "Hyderabad"}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] is True


def test_analyze_point_end_to_end(client, fake_meteo):
    r = client.get("/api/analyze", params=POINT)
    assert r.status_code == 200
    body = r.json()
    assert body["cache"]["analysis_cache"] == "miss"
    assert len(body["yearly"]) == 31
    assert body["threshold"]["method"] == "fixed"
    assert body["quality"]["out_of_range_removed"] == 1
    assert body["quality"]["interpolated"] >= 1
    # synthetic data warms 0.4 C/decade, so heatwaves should increase significantly
    assert body["insights"]["headline"]["kind"] == "up"
    assert body["trends"]["events"]["mann_kendall"]["p_value"] < 0.05
    assert all(1990 <= e["year"] <= 2020 for e in body["events"])
    assert fake_meteo.calls == 1


def test_second_request_is_served_from_cache(client, fake_meteo):
    client.get("/api/analyze", params=POINT)
    r = client.get("/api/analyze", params=POINT)
    assert r.json()["cache"]["analysis_cache"] == "hit"
    # different years, same grid point -> new analysis but no new download
    r = client.get("/api/analyze", params={**POINT, "start_year": 1960, "end_year": 2000})
    assert r.json()["cache"]["weather_cache"] == "hit"
    assert fake_meteo.calls == 1


def test_box_uses_multiple_grid_points_in_one_call(client, fake_meteo):
    r = client.get("/api/analyze", params={"south": 16, "west": 77, "north": 19, "east": 80,
                                           "start_year": 1990, "end_year": 2020})
    assert r.status_code == 200
    assert len(r.json()["meta"]["region"]["grid_points"]) == 9
    assert fake_meteo.calls == 1


def test_calendar_day_method(client):
    r = client.get("/api/analyze", params={**POINT, "threshold_method": "calendar_day"})
    assert r.status_code == 200
    thr = r.json()["threshold"]
    assert thr["max"] > thr["min"]


@pytest.mark.parametrize("params,fragment", [
    ({"lat": 17, "start_year": 1990, "end_year": 2020}, "either lat+lon"),
    ({"lat": 95, "lon": 78}, "less than or equal to 90"),
    ({**POINT, "south": 1, "west": 1, "north": 2, "east": 2}, "not both"),
    ({"south": 0, "west": 0, "north": 20, "east": 5}, "too large"),
    ({"south": 5, "west": 0, "north": 1, "east": 5}, "south must be less"),
    ({**POINT, "start_year": 2015, "end_year": 2020}, "at least 10 years"),
    ({**POINT, "end_year": 2999}, "or earlier"),
    ({**POINT, "percentile": 50}, "greater than or equal to 80"),
    ({**POINT, "hazard": "earthquake"}, "hazard"),
    ({**POINT, "lat": "17; drop table"}, "lat"),
])
def test_invalid_input_rejected(client, params, fragment):
    r = client.get("/api/analyze", params=params)
    assert r.status_code == 422
    assert fragment in " ".join(r.json()["detail"])


def test_series_endpoint(client):
    r = client.get("/api/series", params={**POINT, "year": 2010})
    body = r.json()
    assert r.status_code == 200
    assert len(body["dates"]) == 365 == len(body["tmax"]) == len(body["heatwave"])


def test_exports(client):
    csv = client.get("/api/export.csv", params={**POINT, "table": "yearly"})
    assert csv.status_code == 200 and csv.text.startswith("year,events")
    ev = client.get("/api/export.csv", params={**POINT, "table": "events"})
    assert ev.text.startswith("start,end")
    pdf = client.get("/api/export.pdf", params=POINT)
    assert pdf.content[:4] == b"%PDF"
    png = client.get("/api/plot.png", params={**POINT, "metric": "heatwave_days"})
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_region_name_formula_is_neutralised(client):
    r = client.get("/api/analyze", params={**POINT, "region_name": "=HYPERLINK(\"x\")"})
    assert not r.json()["meta"]["region"]["label"].startswith("=")


def test_geocode_cached_and_sanitised(client, fake_geo):
    r1 = client.get("/api/geocode", params={"q": "Hyderabad<script>"})
    r2 = client.get("/api/geocode", params={"q": "hyderabad script"})
    assert r1.status_code == 200 and r1.json()["results"][0]["bbox"]["south"] == 17.2
    assert r2.json()["cached"] is True and fake_geo.calls == 1
    assert client.get("/api/geocode", params={"q": "<>"}).status_code == 422


def test_rate_limit(settings, fake_meteo, fake_geo):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.storage import MemoryStore
    settings.rate_limit_per_minute = 3
    app = create_app(settings, store=MemoryStore(1, 1, 1),
                     client=OpenMeteoClient("http://x", "era5", 5, 0, 1,
                                            transport=httpx.MockTransport(fake_meteo.handler)),
                     geocoder=None)
    with TestClient(app) as c:
        codes = [c.get("/api/series", params={**POINT, "year": 2000}).status_code
                 for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_cors_only_allows_configured_origin(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    bad = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers


# --------------------------------------------------------- upstream client
def test_retries_after_429(fake_meteo):
    fake_meteo.fail_first = 1
    c = OpenMeteoClient("http://x", "era5", 5, 2, 1, transport=httpx.MockTransport(fake_meteo.handler))
    out = asyncio.run(c.fetch_tmax([GridPoint(17.5, 78.5)], date(2000, 1, 1), date(2000, 12, 31)))
    assert len(out[0].values) == 366 and fake_meteo.calls == 2


def test_client_error_is_not_retried():
    calls = {"n": 0}

    def handler(_):
        calls["n"] += 1
        return httpx.Response(400, json={"error": True, "reason": "bad date"})

    c = OpenMeteoClient("http://x", "era5", 5, 3, 1, transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError, match="bad date"):
        asyncio.run(c.fetch_tmax([GridPoint(0, 0)], date(2000, 1, 1), date(2000, 1, 2)))
    assert calls["n"] == 1


def test_upstream_failure_returns_502(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.storage import MemoryStore
    down = OpenMeteoClient("http://x", "era5", 5, 0, 1,
                           transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with TestClient(create_app(settings, store=MemoryStore(1, 1, 1), client=down)) as c:
        r = c.get("/api/analyze", params=POINT)
    assert r.status_code == 502 and "unavailable" in r.json()["detail"][0]
