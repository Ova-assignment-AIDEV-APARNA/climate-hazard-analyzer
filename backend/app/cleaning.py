"""Quality control for daily maximum temperature series.

ERA5 reanalysis is gridded and essentially gap-free, but this module is
written for messy data (station records such as NOAA GHCN-Daily) so the
pipeline does not have to change if the source does. Steps, in order:

 1. Units      Fahrenheit or Kelvin are converted to Celsius.
 2. Calendar   The series is put on a complete daily calendar; any date the
               source skipped becomes an explicit gap (NaN).
 3. Range      Physically impossible values (< -90 C or > 60 C) are removed.
 4. Spikes     A single day that jumps > 12 C away from BOTH neighbours, in
               the same direction, and from the 7-day median, is treated as a
               data error and removed. Real weather changes (cold fronts) are
               step changes, not one-day up-and-down spikes, so they survive.
               Genuine heat extremes are never clipped: removing them would
               remove the very signal we are looking for.
 5. Gaps       Gaps of 1-2 days are filled by linear interpolation. Longer
               gaps stay missing and break heatwave runs (we never invent a
               3+ day hot spell).
 6. Coverage   Years with < 90% valid days are flagged and excluded from
               trend statistics instead of being silently counted as zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

PHYSICAL_MIN_C = -90.0
PHYSICAL_MAX_C = 60.0
SPIKE_THRESHOLD_C = 12.0
MAX_INTERP_GAP = 2
MIN_YEAR_COVERAGE = 0.9


@dataclass
class QualityReport:
    total_days: int = 0
    missing_in_source: int = 0
    out_of_range_removed: int = 0
    spikes_removed: int = 0
    interpolated: int = 0
    still_missing: int = 0
    units_converted_from: str | None = None
    incomplete_years: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def to_celsius(values: np.ndarray, units: str) -> tuple[np.ndarray, str | None]:
    u = (units or "").strip().lower().replace("°", "").replace("deg", "")
    if u in ("f", "fahrenheit"):
        return (values - 32.0) * 5.0 / 9.0, units
    if u in ("k", "kelvin"):
        return values - 273.15, units
    if u in ("c", "celsius", ""):
        return values, None
    raise ValueError(f"Unknown temperature unit: {units!r}")


def _run_lengths(mask: np.ndarray) -> np.ndarray:
    """For each True position, the length of the True run it belongs to."""
    out = np.zeros(len(mask), dtype=int)
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out[i:j] = j - i
            i = j
        else:
            i += 1
    return out


def clean_tmax(series: pd.Series, units: str = "°C") -> tuple[pd.Series, QualityReport]:
    """Return a cleaned daily series (DatetimeIndex, Celsius) and a QC report."""
    rep = QualityReport()
    s = series.astype(float).sort_index()
    s = s[~s.index.duplicated(keep="first")]

    # 1. units
    values, converted = to_celsius(s.to_numpy(), units)
    s = pd.Series(values, index=s.index)
    rep.units_converted_from = converted

    # 2. complete calendar
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    s = s.reindex(full)
    rep.total_days = len(s)
    rep.missing_in_source = int(s.isna().sum())

    # 3. physical range
    bad = (s < PHYSICAL_MIN_C) | (s > PHYSICAL_MAX_C)
    rep.out_of_range_removed = int(bad.sum())
    s[bad] = np.nan

    # 4. isolated one-day spikes
    prev_diff = s - s.shift(1)
    next_diff = s - s.shift(-1)
    med = s.rolling(7, center=True, min_periods=3).median()
    spike_up = (prev_diff > SPIKE_THRESHOLD_C) & (next_diff > SPIKE_THRESHOLD_C)
    spike_dn = (prev_diff < -SPIKE_THRESHOLD_C) & (next_diff < -SPIKE_THRESHOLD_C)
    spike = (spike_up | spike_dn) & ((s - med).abs() > SPIKE_THRESHOLD_C)
    rep.spikes_removed = int(spike.sum())
    s[spike] = np.nan

    # 5. fill only short gaps (pandas' `limit` would also partly fill long gaps)
    missing = s.isna().to_numpy()
    short_gap = missing & (_run_lengths(missing) <= MAX_INTERP_GAP)
    interp = s.interpolate(method="linear", limit_area="inside")
    fill_mask = short_gap & interp.notna().to_numpy()
    s[fill_mask] = interp[fill_mask]
    rep.interpolated = int(fill_mask.sum())
    rep.still_missing = int(s.isna().sum())

    # 6. per-year coverage
    cov = s.notna().groupby(s.index.year).mean()
    rep.incomplete_years = [int(y) for y, c in cov.items() if c < MIN_YEAR_COVERAGE]
    return s, rep


def area_mean(frames: list[pd.Series], weights: np.ndarray) -> pd.Series:
    """Weighted mean across grid points; a day is missing only if all points are."""
    df = pd.concat(frames, axis=1)
    w = np.broadcast_to(weights, df.shape)
    valid = df.notna().to_numpy()
    num = np.nansum(df.to_numpy() * w, axis=1)
    den = (w * valid).sum(axis=1)
    out = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    return pd.Series(out, index=df.index)
