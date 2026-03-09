"""Pytest fixtures and common test data."""
import os

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Clear geo-related ENV before each test to avoid leakage."""
    for key in (
        "NODE_NAME", "NODE_PRIO", "MESH_NODES", "ANCHOR_IPS", "GEO_SOURCE_URL",
        "GEO_BLOCKS_IPV6_URL", "MAP_DIR", "HAPROXY_CFG_PATH", "HAPROXY_SOCKET",
        "STAGE_DELAY_PRIO2_HOURS", "STAGE_DELAY_PRIO3_HOURS", "FETCH_INTERVAL_HOURS",
        "FETCH_RETRIES", "FETCH_RETRY_DELAY_SEC", "GEO_STATUS_PORT",
        "SIZE_DEVIATION_THRESHOLD", "GEO_BLOCKS_URL", "GEO_LOCATIONS_URL",
        "MAIL_ENABLED", "MAIL_HOST", "MAIL_PORT", "MAIL_USE_TLS", "MAIL_USER",
        "MAIL_PASSWORD", "MAIL_FROM", "MAIL_TO",
        "CLUSTER_HEALTH_INTERVAL_HOURS", "CLUSTER_HEALTH_TIMEOUT_SEC",
    ):
        monkeypatch.delenv(key, raising=False)
