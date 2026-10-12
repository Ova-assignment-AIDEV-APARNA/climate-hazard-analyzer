"""Unit tests for cleaning, heatwave detection and trend statistics."""
import numpy as np
import pandas as pd
import pytest

from app.cleaning import area_mean, clean_tmax
from app.hazards.heatwave import compute_threshold, detect_events, yearly_metrics
from app.regions import grid_points_for_box, snap
from app.trends import analyse_series, mann_kendall, sens_slope


def series(values, start="2000-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"), dtype=float)


# ------------------------------------------------------------------ cleaning
class TestCleaning:
    def test_fahrenheit_converted(self):
        s, rep = clean_tmax(series([104.0, 32.0, 50.0]), "°F")
        assert list(s.round(2)) == [40.0, 0.0, 10.0]
        assert rep.units_converted_from == "°F"

    def test_short_gap_filled_long_gap_kept(self):
        vals = [20.0] * 5 + [np.nan] + [20.0] * 5 + [np.nan] * 4 + [20.0] * 5
        s, rep = clean_tmax(series(vals))
        assert rep.interpolated == 1
        assert rep.still_missing == 4  # the 4-day gap is not invented

    def test_missing_calendar_days_become_gaps(self):
        idx = pd.to_datetime(["2000-01-01", "2000-01-02", "2000-01-05"])
        s, rep = clean_tmax(pd.Series([10.0, 11.0, 12.0], index=idx))
        assert len(s) == 5
        assert rep.missing_in_source == 2

    def test_out_of_range_removed(self):
        s, rep = clean_tmax(series([20.0, 20.0, 99.0, 20.0, 20.0, 20.0, 20.0, 20.0]))
        assert rep.out_of_range_removed == 1

    def test_single_day_spike_removed_but_step_change_kept(self):
        spike = [25.0] * 5 + [45.0] + [25.0] * 5
        s, rep = clean_tmax(series(spike))
        assert rep.spikes_removed == 1
        front = [35.0] * 6 + [20.0] * 6  # a real cold front: a step, not a spike
        s, rep = clean_tmax(series(front))
        assert rep.spikes_removed == 0

    def test_area_mean_ignores_missing_points(self):
        a, b = series([10.0, np.nan]), series([20.0, 30.0])
        out = area_mean([a, b], np.array([0.5, 0.5]))
        assert list(out) == [15.0, 30.0]


# ------------------------------------------------------------------ heatwave
def base_year_series(n_years=12, start="1991-01-01"):
    idx = pd.date_range(start, periods=365 * n_years, freq="D")
    rng = np.random.default_rng(0)
    return pd.Series(30 + rng.normal(0, 1, len(idx)), index=idx)


class TestHeatwave:
    def test_fixed_threshold_is_percentile_of_baseline(self):
        s = base_year_series()
        thr = compute_threshold(s, (1991, 2000), 95)
        base = s[(s.index.year >= 1991) & (s.index.year <= 2000)]
        assert thr.iloc[0] == pytest.approx(np.percentile(base, 95))

    def test_detects_runs_of_min_length_only(self):
        s = series([20, 40, 40, 20, 40, 40, 40, 20, 40, 40, 40, 40, 40, 20])
        thr = pd.Series(30.0, index=s.index)
        events = detect_events(s, thr, 3)
        assert [e.duration for e in events] == [3, 5]
        assert events[0].peak_tmax == 40
        assert events[1].cumulative_excess == pytest.approx(50.0)

    def test_equal_to_threshold_is_not_hot(self):
        s = series([30, 30, 30, 30])
        assert detect_events(s, pd.Series(30.0, index=s.index), 3) == []

    def test_missing_day_breaks_run(self):
        s = series([40, 40, np.nan, 40, 40])
        assert detect_events(s, pd.Series(30.0, index=s.index), 3) == []

    def test_calendar_day_threshold_varies_with_season(self):
        idx = pd.date_range("1991-01-01", "2002-12-31", freq="D")
        seasonal = 25 + 10 * np.sin(2 * np.pi * (idx.dayofyear - 80) / 365)
        s = pd.Series(seasonal + np.random.default_rng(1).normal(0, 1, len(idx)), index=idx)
        thr = compute_threshold(s, (1991, 2000), 95, "calendar_day")
        assert thr["1995-06-20"] > thr["1995-12-20"] + 10

    def test_event_crossing_new_year_counted_in_start_year(self):
        s = series([40] * 4, start="1999-12-30")
        thr = pd.Series(30.0, index=s.index)
        events = detect_events(s, thr, 3)
        rows = yearly_metrics(s, thr, events, range(1999, 2001), set())
        assert rows[0]["events"] == 1 and rows[1]["events"] == 0
        assert rows[0]["heatwave_days"] == 2 and rows[1]["heatwave_days"] == 2

    def test_incomplete_year_gives_nulls_not_zeros(self):
        s = series([20.0] * 730, start="2000-01-01")
        thr = pd.Series(30.0, index=s.index)
        rows = yearly_metrics(s, thr, [], range(2000, 2002), {2001})
        assert rows[0]["events"] == 0 and rows[1]["events"] is None


# -------------------------------------------------------------------- trends
class TestTrends:
    def test_sen_slope_exact_on_line(self):
        x = np.arange(2000, 2020, dtype=float)
        assert sens_slope(x, 2 * x + 1)["slope_per_year"] == pytest.approx(2.0)

    def test_mann_kendall_detects_trend(self):
        rng = np.random.default_rng(3)
        y = np.arange(30) * 0.2 + rng.normal(0, 1, 30)
        mk = mann_kendall(y)
        assert mk["trend"] == "increasing" and mk["p_value"] < 0.01

    def test_mann_kendall_on_noise_is_not_significant(self):
        y = np.random.default_rng(7).normal(0, 1, 40)
        assert mann_kendall(y)["trend"] == "no trend"

    def test_mann_kendall_matches_reference_value(self):
        # S for [1,2,3,4,5] is 10; var = 5*4*15/18 = 16.67; z = 9/4.08 = 2.205
        mk = mann_kendall(np.array([1, 2, 3, 4, 5], dtype=float))
        assert mk["s"] == 10 and mk["z"] == pytest.approx(2.2045, abs=1e-3)

    def test_constant_series(self):
        assert mann_kendall(np.zeros(15))["p_value"] == 1.0

    def test_missing_years_skipped(self):
        years = list(range(2000, 2015))
        vals = [float(i) for i in range(15)]
        vals[3] = None
        out = analyse_series(years, vals, 5)
        assert out["n_years"] == 14 and out["sen"]["slope_per_year"] == pytest.approx(1.0)

    def test_too_few_years_no_test(self):
        out = analyse_series(list(range(2000, 2005)), [1.0] * 5, 5)
        assert out["mann_kendall"] is None


# ------------------------------------------------------------------- regions
def test_snap_and_box_sampling():
    assert snap(17.385) == 17.5 and snap(78.4867) == 78.5
    pts = grid_points_for_box(16.0, 77.0, 19.0, 81.0, 3)
    assert len(pts) == 9
    small = grid_points_for_box(17.3, 78.3, 17.4, 78.4, 3)
    assert len(small) == 1
