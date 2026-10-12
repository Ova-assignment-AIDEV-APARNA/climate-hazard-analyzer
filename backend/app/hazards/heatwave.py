"""Heatwave detection.

Definition (from the brief, made precise):

  A heatwave is a run of at least `min_days` consecutive days on which the
  daily maximum temperature is strictly above the `percentile`-th percentile
  of daily maximum temperature for that region.

Choices that the brief leaves open, and what we do:

* Which period the percentile comes from.  A fixed reference ("baseline")
  period, 1991-2020 by default (the current WMO climate normal). If the
  threshold were computed from the user's selected range instead, changing
  the range would move the threshold and the counts, and comparing regions
  or ranges would be meaningless.

* Fixed vs. calendar-day threshold.
    fixed         One number for the whole year. Matches the brief and the
                  everyday meaning of "heatwave": it only fires in the hot
                  season.
    calendar_day  A threshold for each day of the year, from a 15-day window
                  centred on that day across the baseline years (as in the
                  ETCCDI WSDI / Russo HWMId family). Detects "unusually hot
                  for the time of year", including warm spells in winter.

* Missing days break a run: we never assume an unknown day was hot.

* An event that crosses New Year is counted in the year it started; its
  days are counted in the calendar year they fall in.

Per event we record duration, peak temperature, mean excess over the
threshold, and cumulative excess (degree-days above threshold), which is the
intensity measure used in the charts.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..models import UserFacingError

CALENDAR_WINDOW_DAYS = 15


@dataclass
class Event:
    start: pd.Timestamp
    end: pd.Timestamp
    duration: int
    peak_tmax: float
    mean_excess: float
    cumulative_excess: float

    def as_dict(self) -> dict:
        return {
            "start": self.start.date().isoformat(),
            "end": self.end.date().isoformat(),
            "year": int(self.start.year),
            "duration": self.duration,
            "peak_tmax": round(self.peak_tmax, 2),
            "mean_excess": round(self.mean_excess, 2),
            "cumulative_excess": round(self.cumulative_excess, 2),
        }


def _day_of_year_365(index: pd.DatetimeIndex) -> np.ndarray:
    """Day of year 1..365, with Feb 29 sharing Feb 28's slot."""
    doy = index.dayofyear.to_numpy().copy()
    leap_after_feb = index.is_leap_year & (index.month > 2)
    doy[leap_after_feb] -= 1
    return doy


def compute_threshold(s: pd.Series, baseline: tuple[int, int], percentile: float,
                      method: str = "fixed") -> pd.Series:
    """Threshold for every day of `s` (same index)."""
    base = s[(s.index.year >= baseline[0]) & (s.index.year <= baseline[1])].dropna()
    if len(base) < 365 * 5:
        raise UserFacingError("Not enough valid data in the baseline period to compute a threshold.")

    if method == "fixed":
        value = float(np.percentile(base.to_numpy(), percentile))
        return pd.Series(value, index=s.index)

    if method == "calendar_day":
        base_doy = _day_of_year_365(base.index)
        half = CALENDAR_WINDOW_DAYS // 2
        values = base.to_numpy()
        by_doy = np.empty(366)
        for d in range(1, 366):
            # circular distance so late December and early January are neighbours
            dist = np.abs(base_doy - d)
            dist = np.minimum(dist, 365 - dist)
            by_doy[d] = np.percentile(values[dist <= half], percentile)
        return pd.Series(by_doy[_day_of_year_365(s.index)], index=s.index)

    raise ValueError(f"Unknown threshold method: {method}")


def detect_events(s: pd.Series, threshold: pd.Series, min_days: int) -> list[Event]:
    hot = (s > threshold).to_numpy()  # NaN compares False: missing days break runs
    values = s.to_numpy()
    thr = threshold.to_numpy()
    idx = s.index
    events: list[Event] = []
    i, n = 0, len(hot)
    while i < n:
        if not hot[i]:
            i += 1
            continue
        j = i
        while j < n and hot[j]:
            j += 1
        length = j - i
        if length >= min_days:
            excess = values[i:j] - thr[i:j]
            events.append(Event(
                start=idx[i], end=idx[j - 1], duration=length,
                peak_tmax=float(values[i:j].max()),
                mean_excess=float(excess.mean()),
                cumulative_excess=float(excess.sum()),
            ))
        i = j
    return events


METRICS = {
    "events": "Heatwave events",
    "heatwave_days": "Heatwave days",
    "max_duration": "Longest heatwave (days)",
    "cumulative_excess": "Cumulative intensity (°C·days above threshold)",
    "mean_peak_tmax": "Mean peak Tmax of events (°C)",
}


def yearly_metrics(s: pd.Series, threshold: pd.Series, events: list[Event],
                   years: range, incomplete_years: set[int]) -> list[dict]:
    """One row per year. Years with poor data coverage get nulls, not zeros."""
    hw_day = pd.Series(False, index=s.index)
    excess = (s - threshold).clip(lower=0)
    for e in events:
        hw_day[e.start:e.end] = True

    rows = []
    for y in years:
        in_year = s.index.year == y
        coverage = float(s[in_year].notna().mean()) if in_year.any() else 0.0
        row = {"year": y, "coverage": round(coverage, 3), "complete": y not in incomplete_years}
        if not row["complete"]:
            row.update({k: None for k in METRICS})
            rows.append(row)
            continue
        started = [e for e in events if e.start.year == y]
        row["events"] = len(started)
        row["heatwave_days"] = int(hw_day[in_year].sum())
        row["max_duration"] = max((e.duration for e in started), default=0)
        row["cumulative_excess"] = round(float(excess[in_year & hw_day.to_numpy()].sum()), 2)
        row["mean_peak_tmax"] = (round(float(np.mean([e.peak_tmax for e in started])), 2)
                                 if started else None)
        rows.append(row)
    return rows
