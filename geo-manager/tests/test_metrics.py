"""Tests for metrics.py."""
from datetime import datetime, timezone

import pytest

from geo_manager.config import Config
from geo_manager.metrics import (
    get_last_validated,
    inc_fail_open_events,
    inc_fetch_fail_open,
    inc_fetch_failure,
    inc_fetch_success,
    inc_reload_failure,
    inc_reload_success,
    inc_validation_failure,
    reset_for_tests,
    set_last_validated,
    to_prometheus,
)


def test_to_prometheus_always_has_node_identity():
    """to_prometheus returns at least geo_node_prio, geo_is_master, geo_last_validated."""
    config = Config.from_env()
    out = to_prometheus(config)
    assert "geo_node_prio " in out
    assert "geo_is_master " in out
    assert "geo_last_validated_timestamp_seconds " in out
    assert "geo_fetch_total" in out
    assert "geo_reload_success_total" in out
    assert "geo_reload_failure_total" in out
    assert "geo_fail_open_events_total" in out


def test_to_prometheus_is_master():
    config = Config.from_env()
    config.node_prio = 1
    out = to_prometheus(config)
    assert "geo_is_master 1" in out


def test_to_prometheus_is_follower():
    config = Config.from_env()
    config.node_prio = 2
    out = to_prometheus(config)
    assert "geo_is_master 0" in out


def test_inc_fetch_success_increments():
    reset_for_tests()
    config = Config.from_env()
    inc_fetch_success()
    inc_fetch_success()
    out = to_prometheus(config)
    assert 'geo_fetch_total{outcome="success"} 2' in out


def test_inc_fetch_failure_increments():
    reset_for_tests()
    config = Config.from_env()
    inc_fetch_failure()
    out = to_prometheus(config)
    assert 'geo_fetch_total{outcome="failure"} 1' in out


def test_inc_fetch_fail_open_increments():
    reset_for_tests()
    config = Config.from_env()
    inc_fetch_fail_open()
    out = to_prometheus(config)
    assert 'geo_fetch_total{outcome="fail_open"} 1' in out


def test_inc_validation_failure_reasons():
    reset_for_tests()
    config = Config.from_env()
    inc_validation_failure("size")
    inc_validation_failure("anchor")
    inc_validation_failure("syntax")
    inc_validation_failure("size")
    out = to_prometheus(config)
    assert 'geo_validation_failures_total{reason="anchor"} 1' in out
    assert 'geo_validation_failures_total{reason="size"} 2' in out
    assert 'geo_validation_failures_total{reason="syntax"} 1' in out


def test_inc_reload_success_failure():
    reset_for_tests()
    config = Config.from_env()
    inc_reload_success()
    inc_reload_success()
    inc_reload_failure()
    out = to_prometheus(config)
    assert "geo_reload_success_total 2" in out
    assert "geo_reload_failure_total 1" in out


def test_inc_fail_open_events():
    reset_for_tests()
    config = Config.from_env()
    inc_fail_open_events()
    inc_fail_open_events()
    out = to_prometheus(config)
    assert "geo_fail_open_events_total 2" in out


def test_set_get_last_validated():
    set_last_validated(datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc))
    got = get_last_validated()
    assert got is not None
    assert got.year == 2026
    assert got.month == 3


def test_last_validated_timestamp_in_prometheus():
    reset_for_tests()
    config = Config.from_env()
    set_last_validated(datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc))
    out = to_prometheus(config)
    assert "geo_last_validated_timestamp_seconds " in out
    for line in out.splitlines():
        if line.startswith("geo_last_validated_timestamp_seconds "):
            _, val = line.split(None, 1)
            assert int(val) > 0
            break
