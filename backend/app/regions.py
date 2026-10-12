"""Turn a user region into the ERA5 grid points we fetch.

ERA5 is a gridded reanalysis with 0.25 degree spacing (about 28 km at the
equator). Snapping every coordinate to that grid means:
  * two clicks a few hundred metres apart reuse the same cached series, and
  * cache keys are built from numbers we generated, never from user text.

A box is represented by an evenly spaced sample of at most N x N grid points.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

GRID_STEP = 0.25


def snap(value: float, step: float = GRID_STEP) -> float:
    return round(round(value / step) * step, 4)


@dataclass(frozen=True)
class GridPoint:
    lat: float
    lon: float

    @property
    def key(self) -> str:
        return f"{self.lat:.2f}:{self.lon:.2f}"


def _axis_samples(lo: float, hi: float, max_points: int) -> list[float]:
    span = hi - lo
    # One sample per grid step at most, capped at max_points per axis.
    n = max(1, min(max_points, int(span / GRID_STEP) + 1))
    if n == 1:
        return [snap((lo + hi) / 2)]
    return [snap(v) for v in np.linspace(lo, hi, n)]


def grid_points_for_point(lat: float, lon: float) -> list[GridPoint]:
    return [GridPoint(snap(lat), snap(lon))]


def grid_points_for_box(south: float, west: float, north: float, east: float,
                        max_points_per_axis: int) -> list[GridPoint]:
    lats = _axis_samples(south, north, max_points_per_axis)
    lons = _axis_samples(west, east, max_points_per_axis)
    pts = {GridPoint(la, lo) for la in lats for lo in lons}
    return sorted(pts, key=lambda p: (p.lat, p.lon))


def area_weights(points: list[GridPoint]) -> np.ndarray:
    """Grid cells shrink toward the poles; weight by cos(latitude)."""
    w = np.array([math.cos(math.radians(p.lat)) for p in points])
    return w / w.sum()
