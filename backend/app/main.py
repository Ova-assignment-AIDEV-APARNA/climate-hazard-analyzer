"""FastAPI application: routes, middleware and error handling.

Endpoints (all GET, so responses can be cached by browsers and CDNs):

  /api/health          liveness + database check
  /api/geocode?q=      place search (city, state, country) -> points and boxes
  /api/analyze         full heatwave analysis as JSON
  /api/series?year=    daily Tmax + threshold + heatwave flags for one year
  /api/plot.png        server-rendered trend chart for one metric
  /api/export.csv      yearly table or event list as CSV
  /api/export.pdf      printable report
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from . import exports
from .config import Settings, get_settings
from .hazards.heatwave import METRICS
from .models import AnalysisParams, UserFacingError, analysis_params_dep
from .ratelimit import RateLimiter, client_ip
from .service import AnalysisService
from .sources.geocode import Geocoder, normalise_query
from .sources.open_meteo import OpenMeteoClient, UpstreamError
from .storage import MemoryStore, MongoStore

log = logging.getLogger("climate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, *, store=None, client=None, geocoder=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal store, client, geocoder
        if store is None:
            if settings.mongo_uri:
                store = MongoStore(settings.mongo_uri, settings.mongo_db, settings.weather_cache_days,
                                   settings.analysis_cache_days, settings.geocode_cache_days)
                await store.init_indexes()
                log.info("Using MongoDB store")
            else:
                store = MemoryStore(settings.weather_cache_days, settings.analysis_cache_days,
                                    settings.geocode_cache_days)
                log.warning("MONGO_URI not set: using in-memory store (data is lost on restart)")
        client = client or OpenMeteoClient(settings.open_meteo_url, settings.open_meteo_model,
                                           settings.upstream_timeout_s, settings.upstream_max_retries,
                                           settings.upstream_concurrency)
        geocoder = geocoder or Geocoder(settings.nominatim_url, settings.nominatim_user_agent)
        app.state.store = store
        app.state.geocoder = geocoder
        app.state.service = AnalysisService(settings, store, client)
        yield
        await client.close()
        await geocoder.close()
        await store.close()

    app = FastAPI(title="Climate Hazard Trend Analyzer", version="1.0", lifespan=lifespan)

    # ---------------------------------------------------------- middleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
        expose_headers=["Retry-After", "Content-Disposition"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    # ---------------------------------------------------------- rate limits
    light = RateLimiter(settings.rate_limit_per_minute)
    heavy = RateLimiter(settings.heavy_rate_limit_per_minute)

    def limit(limiter: RateLimiter):
        def dep(request: Request):
            wait = limiter.check(client_ip(request, settings.trusted_proxy_hops))
            if wait:
                raise HTTPException(429, "Too many requests. Please slow down.",
                                    headers={"Retry-After": str(int(wait) + 1)})
        return dep

    # -------------------------------------------------------------- errors
    def _messages(errors) -> list[str]:
        out = []
        for e in errors:
            loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("query", "body"))
            msg = str(e.get("msg", "invalid value")).removeprefix("Value error, ")
            out.append(f"{loc}: {msg}" if loc else msg)
        return out

    @app.exception_handler(ValidationError)
    async def pydantic_error(_: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"detail": _messages(exc.errors())})

    @app.exception_handler(RequestValidationError)
    async def request_error(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": _messages(exc.errors())})

    @app.exception_handler(UpstreamError)
    async def upstream_error(_: Request, exc: UpstreamError):
        return JSONResponse(status_code=502, content={"detail": [str(exc)]})

    @app.exception_handler(UserFacingError)
    async def user_error(_: Request, exc: UserFacingError):
        return JSONResponse(status_code=422, content={"detail": [str(exc)]})

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        log.exception("Unhandled error")  # full trace in logs, never in the response
        return JSONResponse(status_code=500, content={"detail": ["Internal error. Please try again."]})

    # --------------------------------------------------------------- routes
    def svc(request: Request) -> AnalysisService:
        return request.app.state.service

    @app.get("/api/health")
    async def health(request: Request):
        db_ok = await request.app.state.store.ping()
        return {"status": "ok" if db_ok else "degraded", "database": db_ok,
                "last_complete_year": settings.last_complete_year}

    @app.get("/api/geocode", dependencies=[Depends(limit(light))])
    async def geocode(request: Request, q: str = Query(..., min_length=2, max_length=100)):
        query = normalise_query(q)
        if len(query) < 2:
            raise HTTPException(422, "Search text must contain letters or numbers.")
        store = request.app.state.store
        key = "geo:" + query
        cached = await store.get_geocode(key)
        if cached is not None:
            return {"results": cached, "cached": True}
        results = await request.app.state.geocoder.search(query)
        await store.put_geocode(key, results)
        return {"results": results, "cached": False}

    @app.get("/api/analyze", dependencies=[Depends(limit(heavy))])
    async def analyze(response: Response, p: AnalysisParams = Depends(analysis_params_dep),
                      service: AnalysisService = Depends(svc)):
        result = await service.analyze(p)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return result

    @app.get("/api/series", dependencies=[Depends(limit(light))])
    async def series(response: Response, year: int = Query(..., ge=1950),
                     p: AnalysisParams = Depends(analysis_params_dep),
                     service: AnalysisService = Depends(svc)):
        if year > settings.last_complete_year:
            raise HTTPException(422, f"year must be {settings.last_complete_year} or earlier.")
        response.headers["Cache-Control"] = "public, max-age=3600"
        return await service.daily_series(p, year)

    @app.get("/api/plot.png", dependencies=[Depends(limit(heavy))])
    async def plot(metric: Literal["events", "heatwave_days", "max_duration", "cumulative_excess",
                                   "mean_peak_tmax"] = "events",
                   p: AnalysisParams = Depends(analysis_params_dep),
                   service: AnalysisService = Depends(svc)):
        result = await service.analyze(p)
        png = exports.trend_png(result, metric)
        return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/export.csv", dependencies=[Depends(limit(heavy))])
    async def export_csv(table: Literal["yearly", "events"] = "yearly",
                         p: AnalysisParams = Depends(analysis_params_dep),
                         service: AnalysisService = Depends(svc)):
        result = await service.analyze(p)
        name = f"heatwave_{table}_{p.start_year}-{p.end_year}.csv"
        return Response(exports.to_csv(result, table), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/export.pdf", dependencies=[Depends(limit(heavy))])
    async def export_pdf(p: AnalysisParams = Depends(analysis_params_dep),
                         service: AnalysisService = Depends(svc)):
        result = await service.analyze(p)
        name = f"heatwave_report_{p.start_year}-{p.end_year}.pdf"
        return Response(exports.to_pdf(result), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/metrics")
    async def metrics():
        return METRICS

    return app


app = create_app()
