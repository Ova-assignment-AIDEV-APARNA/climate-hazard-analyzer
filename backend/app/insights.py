"""Plain-language insights generated from the statistics.

Rules this module follows so the sentences stay honest:

* The headline uses Mann-Kendall significance (p < 0.05). A non-significant
  trend is reported as "no statistically significant trend", never as an
  increase, even if the fitted line slopes upward.
* Percent changes come from the Sen's-slope trend line (start vs end value),
  not from the first and last single years, which are noisy.
* No percentage is given when the starting level is small (< 1 per year):
  going from 0.1 to 0.4 events/year is "+300%" and means very little.
* Caveats are generated from the data (short record, rare events, gaps,
  box averaging) rather than pasted in as boilerplate.
"""
from __future__ import annotations

import numpy as np

from .hazards.heatwave import METRICS

SIGNIFICANCE = 0.05
MIN_BASE_FOR_PERCENT = 1.0

UNITS = {
    "events": "events per year",
    "heatwave_days": "heatwave days per year",
    "max_duration": "days",
    "cumulative_excess": "°C·days per year",
    "mean_peak_tmax": "°C",
}

NOUN = {
    "events": "Heatwave frequency",
    "heatwave_days": "The number of heatwave days",
    "max_duration": "The length of the longest yearly heatwave",
    "cumulative_excess": "Heatwave intensity (cumulative heat above the threshold)",
    "mean_peak_tmax": "The peak temperature reached during heatwaves",
}


def _fmt_p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def trend_change(trend: dict, start: int, end: int) -> dict | None:
    if not trend or not trend.get("sen"):
        return None
    sen = trend["sen"]
    fit_start = sen["intercept"] + sen["slope_per_year"] * start
    fit_end = sen["intercept"] + sen["slope_per_year"] * end
    pct = None
    if fit_start >= MIN_BASE_FOR_PERCENT:
        pct = (fit_end - fit_start) / fit_start * 100
    return {"fit_start": fit_start, "fit_end": fit_end, "change": fit_end - fit_start, "percent": pct}


def metric_sentence(metric: str, trend: dict, start: int, end: int, region: str) -> dict | None:
    ch = trend_change(trend, start, end)
    if ch is None:
        return None
    mk = trend["mann_kendall"]
    p, direction = mk["p_value"], mk["trend"]
    per_decade = trend["sen"]["slope_per_year"] * 10
    unit = UNITS[metric]
    rng = f"{ch['fit_start']:.1f} to {ch['fit_end']:.1f} {unit}"
    stats_txt = f"Sen's slope {per_decade:+.2f} per decade, Mann-Kendall {_fmt_p(p)}"

    if direction == "no trend":
        text = (f"{NOUN[metric]} in {region} shows no statistically significant trend "
                f"between {start} and {end} ({stats_txt}). The trend line moves from {rng}, "
                f"but year-to-year variation is too large to rule out chance.")
        kind = "neutral"
    else:
        verb = "increased" if direction == "increasing" else "decreased"
        pct = f" ({ch['percent']:+.0f}%)" if ch["percent"] is not None else ""
        text = (f"{NOUN[metric]} in {region} has {verb} since {start}: the trend line goes "
                f"from {rng}{pct} by {end} ({stats_txt}).")
        kind = "up" if direction == "increasing" else "down"
    return {
        "metric": metric, "kind": kind, "text": text,
        "percent_change": None if ch["percent"] is None else round(ch["percent"], 1),
        "change": round(ch["change"], 3), "p_value": round(p, 4),
        "significant": direction != "no trend",
    }


def build_insights(*, region: str, params, yearly: list[dict], trends: dict,
                   events: list[dict], quality: dict, threshold: dict, is_box: bool,
                   n_points: int) -> dict:
    start, end = params.start_year, params.end_year
    headline = metric_sentence("events", trends["events"], start, end, region)
    supporting = [s for s in (metric_sentence(m, trends[m], start, end, region)
                              for m in ("heatwave_days", "cumulative_excess", "max_duration"))
                  if s]

    facts = []
    complete = [r for r in yearly if r["complete"]]
    if end - start + 1 >= 20 and complete:
        first = [r["heatwave_days"] for r in complete if r["year"] < start + 10]
        last = [r["heatwave_days"] for r in complete if r["year"] > end - 10]
        if first and last:
            facts.append(
                f"In {end - 9}-{end} the region averaged {np.mean(last):.1f} heatwave days a year, "
                f"compared with {np.mean(first):.1f} in {start}-{start + 9}.")
    if events:
        longest = max(events, key=lambda e: (e["duration"], e["cumulative_excess"]))
        hottest = max(events, key=lambda e: e["peak_tmax"])
        facts.append(f"Longest heatwave: {longest['duration']} days, {longest['start']} to {longest['end']}.")
        facts.append(f"Highest temperature during a heatwave: {hottest['peak_tmax']:.1f} °C "
                     f"(event starting {hottest['start']}).")
    else:
        facts.append("No heatwaves met the definition in the selected period.")

    caveats = []
    n_years = end - start + 1
    if n_years < 30:
        caveats.append(f"Only {n_years} years are selected. Trends over fewer than 30 years are "
                       "sensitive to which years happen to start and end the range.")
    counts = [r["events"] for r in complete]
    if counts and np.mean(counts) < 1:
        caveats.append(f"Heatwaves are rare here (about {np.mean(counts):.1f} a year), so small "
                       "changes look large in percentage terms. Read the absolute numbers.")
    if quality["incomplete_years"]:
        yrs = [y for y in quality["incomplete_years"] if start <= y <= end]
        if yrs:
            caveats.append(f"Years with under 90% valid data were left out of the trend: {', '.join(map(str, yrs))}.")
    if is_box:
        caveats.append(f"The box is represented by the area-weighted average of {n_points} ERA5 grid points. "
                       "Averaging smooths local extremes, so a single town inside the box can see more "
                       "heatwaves than the average shows.")
    caveats.append(f"The threshold ({threshold['description']}) comes from {params.baseline_start}-"
                   f"{params.baseline_end}. Counts describe heat relative to that period's climate, "
                   "not an absolute health-risk level.")
    caveats.append("Data is ERA5 reanalysis on a ~28 km grid. Its daily maximum is usually a little "
                   "lower than a weather station's, especially in cities (urban heat is not resolved).")
    sig = [s for s in [headline, *supporting] if s and s["significant"]]
    if supporting and len(sig) == 1:
        caveats.append("Four related metrics are tested; with only one significant result, treat "
                       "the evidence for a trend as weak.")
    caveats.append("A trend shows what changed, not why. Attributing it to climate change needs "
                   "model-based attribution studies.")
    return {"headline": headline, "supporting": supporting, "facts": facts, "caveats": caveats}
