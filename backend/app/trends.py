"""Trend statistics for a yearly series.

Three complementary views:

* Rolling mean      A centred moving average, for the chart only (it shows
                    the shape, it is not a test).
* OLS regression    Slope, its p-value and R^2. Familiar, but it assumes
                    normally distributed residuals, which yearly event counts
                    (small integers, many zeros) usually are not.
* Mann-Kendall      Non-parametric test for a monotonic trend; robust to
  + Sen's slope     outliers and non-normal data, and the standard choice in
                    climate-extremes papers. Sen's slope is the median of all
                    pairwise slopes. We use these for the headline insight.

Known limitation: neither test corrects for serial correlation between
years (e.g. Hamed-Rao variance correction). Noted in the README.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats

MIN_YEARS_FOR_TREND = 10


def rolling_mean(years: list[int], values: list[float | None], window: int) -> list[float | None]:
    arr = np.array([np.nan if v is None else v for v in values], dtype=float)
    out = []
    half = window // 2
    for i in range(len(arr)):
        lo, hi = max(0, i - half), min(len(arr), i - half + window)
        chunk = arr[lo:hi]
        chunk = chunk[~np.isnan(chunk)]
        # Require most of the window to be present, so the ends are not noisy.
        out.append(round(float(chunk.mean()), 3) if len(chunk) >= max(3, window * 0.6) else None)
    return out


def ols(x: np.ndarray, y: np.ndarray) -> dict:
    res = stats.linregress(x, y)
    return {
        "slope_per_year": float(res.slope),
        "intercept": float(res.intercept),
        "p_value": float(res.pvalue) if not math.isnan(res.pvalue) else 1.0,
        "r_squared": float(res.rvalue ** 2) if not math.isnan(res.rvalue) else 0.0,
    }


def mann_kendall(y: np.ndarray) -> dict:
    """Two-sided Mann-Kendall test with the standard tie correction."""
    n = len(y)
    s = 0
    for i in range(n - 1):
        s += int(np.sign(y[i + 1:] - y[i]).sum())
    _, counts = np.unique(y, return_counts=True)
    ties = counts[counts > 1]
    var_s = (n * (n - 1) * (2 * n + 5) - np.sum(ties * (ties - 1) * (2 * ties + 5))) / 18.0
    if var_s <= 0:  # every value identical
        return {"s": 0, "z": 0.0, "p_value": 1.0, "tau": 0.0, "trend": "no trend"}
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    p = 2 * (1 - stats.norm.cdf(abs(z)))
    tau = s / (0.5 * n * (n - 1))
    trend = "increasing" if (p < 0.05 and z > 0) else "decreasing" if (p < 0.05 and z < 0) else "no trend"
    return {"s": int(s), "z": float(z), "p_value": float(p), "tau": float(tau), "trend": trend}


def sens_slope(x: np.ndarray, y: np.ndarray) -> dict:
    slopes = [(y[j] - y[i]) / (x[j] - x[i])
              for i in range(len(x) - 1) for j in range(i + 1, len(x)) if x[j] != x[i]]
    slope = float(np.median(slopes))
    intercept = float(np.median(y - slope * x))
    return {"slope_per_year": slope, "intercept": intercept}


def analyse_series(years: list[int], values: list[float | None], window: int) -> dict:
    """Trend summary for one metric. Missing years are skipped, not zero-filled."""
    pairs = [(yr, v) for yr, v in zip(years, values) if v is not None]
    result = {
        "rolling_mean": rolling_mean(years, values, window),
        "rolling_window": window,
        "n_years": len(pairs),
        "ols": None, "mann_kendall": None, "sen": None,
    }
    if len(pairs) < MIN_YEARS_FOR_TREND:
        result["note"] = f"Fewer than {MIN_YEARS_FOR_TREND} usable years; no trend test."
        return result
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    result["ols"] = ols(x, y)
    result["mann_kendall"] = mann_kendall(y)
    result["sen"] = sens_slope(x, y)
    return result
