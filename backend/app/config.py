"""Application settings, read from environment variables (or a .env file).

Every tunable that matters for security or cost lives here so it can be
changed per deployment without touching code.
"""
from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -----------------------------------------------------------
    # Empty MONGO_URI -> in-memory store (local dev and tests only).
    mongo_uri: str = ""
    mongo_db: str = "climate_hazards"

    # --- CORS ---------------------------------------------------------------
    # Comma-separated list of origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:5173"

    # --- Upstream data source (Open-Meteo historical archive, ERA5) ----------
    open_meteo_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_model: str = "era5"
    data_start: date = date(1950, 1, 1)
    upstream_timeout_s: float = 45.0
    upstream_max_retries: int = 3
    upstream_concurrency: int = 2  # simultaneous calls we allow ourselves

    # --- Geocoding (OpenStreetMap Nominatim) ---------------------------------
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    # Nominatim's usage policy requires an identifying User-Agent.
    nominatim_user_agent: str = "climate-hazard-analyzer/1.0 (case-study demo)"

    # --- Caching ------------------------------------------------------------
    weather_cache_days: int = 90  # ERA5 history barely changes; refresh rarely
    analysis_cache_days: int = 7
    geocode_cache_days: int = 30

    # --- Rate limiting (per client IP, token bucket) -------------------------
    rate_limit_per_minute: int = 60  # cheap endpoints
    heavy_rate_limit_per_minute: int = 12  # analyze / export endpoints
    # How many reverse proxies sit in front of the app and append to
    # X-Forwarded-For. Render has one. 0 = ignore the header entirely.
    trusted_proxy_hops: int = 0

    # --- Region limits --------------------------------------------------------
    max_bbox_span_deg: float = 10.0
    max_grid_points_per_axis: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def last_complete_year(self) -> int:
        """Only analyse whole calendar years; the current year is partial."""
        return date.today().year - 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
