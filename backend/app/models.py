"""Request validation.

All user input enters through `AnalysisParams`. Pydantic rejects anything out
of range before it reaches the data layer, and the region is reduced to
plain floats, so nothing user-typed is ever used as a database key or
forwarded to an upstream API as raw text.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional

from fastapi import Query
from pydantic import BaseModel, Field, field_validator, model_validator

from .config import get_settings

# Bump this whenever detection or trend logic changes, so cached analyses
# computed by older code are not served.
ANALYSIS_VERSION = "1.0"


class UserFacingError(ValueError):
    """A problem with the request or data that is safe to show to the user."""


class Hazard(str, Enum):
    heatwave = "heatwave"
    # Planned: drought, heavy_rain. The UI lists them as "coming soon".


class ThresholdMethod(str, Enum):
    fixed = "fixed"  # one percentile over all baseline days (the brief's definition)
    calendar_day = "calendar_day"  # percentile per day-of-year, 15-day window


class AnalysisParams(BaseModel):
    """Everything that determines an analysis result (and its cache key)."""

    # Region: either a point (lat + lon) or a box (south, west, north, east).
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    south: Optional[float] = Field(None, ge=-90, le=90)
    west: Optional[float] = Field(None, ge=-180, le=180)
    north: Optional[float] = Field(None, ge=-90, le=90)
    east: Optional[float] = Field(None, ge=-180, le=180)
    region_name: str = Field("", max_length=120)

    hazard: Hazard = Hazard.heatwave
    start_year: int = Field(1990, ge=1950)
    end_year: int = Field(2020, ge=1950)
    percentile: float = Field(95.0, ge=80.0, le=99.5)
    min_days: int = Field(3, ge=2, le=10)
    threshold_method: ThresholdMethod = ThresholdMethod.fixed
    baseline_start: int = Field(1991, ge=1950)
    baseline_end: int = Field(2020, ge=1950)

    @field_validator("region_name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        # Display-only label. Keep printable characters and strip anything a
        # spreadsheet could interpret as a formula when exported to CSV.
        v = "".join(ch for ch in v if ch.isprintable()).strip()
        return v.lstrip("=+-@\t\r")

    @model_validator(mode="after")
    def check_region_and_years(self) -> "AnalysisParams":
        s = get_settings()
        is_point = self.lat is not None and self.lon is not None
        box = (self.south, self.west, self.north, self.east)
        is_box = all(v is not None for v in box)
        if is_point == is_box:
            raise ValueError("Give either lat+lon or south+west+north+east, not both.")
        if is_box:
            if self.south >= self.north:
                raise ValueError("south must be less than north.")
            if self.west >= self.east:
                raise ValueError("west must be less than east (boxes crossing the antimeridian are not supported).")
            if (self.north - self.south) > s.max_bbox_span_deg or (self.east - self.west) > s.max_bbox_span_deg:
                raise ValueError(f"Box is too large; each side may span at most {s.max_bbox_span_deg} degrees.")

        last = s.last_complete_year
        for name in ("start_year", "end_year", "baseline_start", "baseline_end"):
            if getattr(self, name) > last:
                raise ValueError(f"{name} must be {last} or earlier (only complete years are analysed).")
        if self.end_year - self.start_year < 9:
            raise ValueError("Choose at least 10 years; shorter ranges cannot support a trend.")
        if self.baseline_end - self.baseline_start < 9:
            raise ValueError("The baseline period must cover at least 10 years.")
        return self

    @property
    def is_box(self) -> bool:
        return self.south is not None

    def cache_key(self) -> str:
        """Stable hash of everything that affects the result."""
        payload = self.model_dump(mode="json", exclude={"region_name"})
        # Round coordinates so tiny map-click differences share a cache entry.
        for k in ("lat", "lon", "south", "west", "north", "east"):
            if payload[k] is not None:
                payload[k] = round(payload[k], 3)
        payload["v"] = ANALYSIS_VERSION
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()


def analysis_params_dep(
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    south: Optional[float] = Query(None),
    west: Optional[float] = Query(None),
    north: Optional[float] = Query(None),
    east: Optional[float] = Query(None),
    region_name: str = Query("", max_length=120),
    hazard: Hazard = Query(Hazard.heatwave),
    start_year: int = Query(1990),
    end_year: int = Query(2020),
    percentile: float = Query(95.0),
    min_days: int = Query(3),
    threshold_method: ThresholdMethod = Query(ThresholdMethod.fixed),
    baseline_start: int = Query(1991),
    baseline_end: int = Query(2020),
) -> AnalysisParams:
    """FastAPI dependency: builds and validates AnalysisParams from the query string.

    Validation errors raised here are turned into 422 responses in main.py.
    """
    return AnalysisParams(
        lat=lat, lon=lon, south=south, west=west, north=north, east=east,
        region_name=region_name, hazard=hazard, start_year=start_year,
        end_year=end_year, percentile=percentile, min_days=min_days,
        threshold_method=threshold_method, baseline_start=baseline_start,
        baseline_end=baseline_end,
    )
