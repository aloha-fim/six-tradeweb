"""Unit tests for the liquidity signal engine (no DB)."""
from __future__ import annotations

from app.analytics import drift, latest_z, regime, sector_gps, stress_score
from app.analytics.liquidity import drift_label, risk_label, stretch_label
from app.clients.history import synthetic_bidask_series


def test_z_zero_on_flat_series():
    assert latest_z([10.0] * 80) == 0.0


def test_z_positive_when_above_history():
    s = [10.0] * 79 + [25.0]
    assert latest_z(s) > 1.0


def test_drift_detects_recent_widening():
    calm = [10.0] * 90
    widening = [10.0] * 70 + [10 + i for i in range(20)]
    assert drift(widening) > drift(calm)


def test_stress_and_labels_monotonic():
    assert stress_score(0, 0) == 50
    assert stress_score(2, 1) > stress_score(0, 0) > stress_score(-2, -1)
    assert regime(1.0) == "stressed" and regime(-1.0) == "easing" and regime(0.0) == "normal"
    assert stretch_label(2.0) == "High" and stretch_label(0.0) == "Low"
    assert drift_label(1.0) == "Rising" and drift_label(-1.0) == "Falling"
    assert risk_label(80) == "High" and risk_label(30) == "Low"


def test_synthetic_series_anchors_to_current():
    s = synthetic_bidask_series("TEST", 50.0, "Agency MBS", days=90)
    assert len(s) == 90 and abs(s[-1] - 50.0) < 1e-6


def test_sector_profiles_rank_as_designed():
    # MBS is the most stressed profile; UST the calmest.
    mbs = sector_gps("Agency MBS", [synthetic_bidask_series(f"M{i}", 10, "Agency MBS") for i in range(4)])
    ust = sector_gps("UST", [synthetic_bidask_series(f"U{i}", 2, "UST") for i in range(4)])
    assert mbs.stress > ust.stress
    assert mbs.z > ust.z
