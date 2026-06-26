"""Shared pytest fixtures backed by synthetic data — no live DB required."""
from __future__ import annotations

import pytest

from app.db.testdata import generate_all_series, generate_capacity_snapshot


@pytest.fixture(scope="session")
def all_series():
    """All six target series over 365 synthetic daily points."""
    return generate_all_series(n_days=365)


@pytest.fixture(scope="session")
def short_series():
    """Same targets, only 60 days — exercises fallback paths in the pipeline."""
    return generate_all_series(n_days=60)


@pytest.fixture(scope="session")
def tiny_series():
    """3-point series — only TrendForecaster should handle these."""
    return generate_all_series(n_days=3)


@pytest.fixture(scope="session")
def snapshot():
    """Capacity snapshot for a 65-server / 4-rack synthetic datacenter."""
    return generate_capacity_snapshot()
