"""Pytest fixtures and common test data."""
import os

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Clear geo-related ENV before each test to avoid leakage."""
    for key in (
        "NODE_NAME", "NODE_PRIO", "MESH_NODES", "ANCHOR_IPS", "GEO_SOURCE_URL",
        "MAP_DIR", "HAPROXY_CFG_PATH", "HAPROXY_SOCKET", "STAGE_DELAY_PRIO2_HOURS",
        "STAGE_DELAY_PRIO3_HOURS", "FETCH_INTERVAL_HOURS", "GEO_STATUS_PORT",
        "SIZE_DEVIATION_THRESHOLD", "GEO_BLOCKS_URL", "GEO_LOCATIONS_URL",
    ):
        monkeypatch.delenv(key, raising=False)
