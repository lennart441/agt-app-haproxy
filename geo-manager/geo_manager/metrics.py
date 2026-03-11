"""
Prometheus metrics for Geo-Manager: fetch, validation, reload, fail-open, node identity.
Thread-safe counters and last-validated timestamp; output combined with cluster health in main.
"""
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

_lock = threading.Lock()
_fetch_success = 0
_fetch_failure = 0
_fetch_fail_open = 0
_validation_failures: dict[str, int] = {}
_reload_success = 0
_reload_failure = 0
_fail_open_events = 0
_last_validated: datetime | None = None


def inc_fetch_success() -> None:
    with _lock:
        global _fetch_success
        _fetch_success += 1


def inc_fetch_failure() -> None:
    with _lock:
        global _fetch_failure
        _fetch_failure += 1


def inc_fetch_fail_open() -> None:
    with _lock:
        global _fetch_fail_open
        _fetch_fail_open += 1


def inc_validation_failure(reason: str) -> None:
    """Reason: size, anchor, or syntax."""
    with _lock:
        if reason not in _validation_failures:
            _validation_failures[reason] = 0
        _validation_failures[reason] += 1


def inc_reload_success() -> None:
    with _lock:
        global _reload_success
        _reload_success += 1


def inc_reload_failure() -> None:
    with _lock:
        global _reload_failure
        _reload_failure += 1


def inc_fail_open_events() -> None:
    with _lock:
        global _fail_open_events
        _fail_open_events += 1


def set_last_validated(dt: datetime) -> None:
    with _lock:
        global _last_validated
        _last_validated = dt


def get_last_validated() -> datetime | None:
    with _lock:
        return _last_validated


def reset_for_tests() -> None:
    """Reset all counters and last_validated. For use in tests only."""
    with _lock:
        global _fetch_success, _fetch_failure, _fetch_fail_open
        global _reload_success, _reload_failure, _fail_open_events, _last_validated
        _fetch_success = 0
        _fetch_failure = 0
        _fetch_fail_open = 0
        _validation_failures.clear()
        _reload_success = 0
        _reload_failure = 0
        _fail_open_events = 0
        _last_validated = None


def to_prometheus(config: "Config") -> str:
    """
    Return Prometheus text format for app metrics (node identity, fetch/reload/fail_open counters).
    Always returns at least geo_node_prio, geo_is_master, geo_last_validated_timestamp_seconds
    so /metrics is never empty.
    """
    lines: list[str] = []
    with _lock:
        lines.append(f"geo_node_prio {config.node_prio}")
        lines.append(f"geo_is_master {1 if config.am_i_master() else 0}")
        ts = _last_validated.timestamp() if _last_validated else 0
        lines.append(f"geo_last_validated_timestamp_seconds {int(ts)}")
        lines.append(f"geo_fetch_total{{outcome=\"success\"}} {_fetch_success}")
        lines.append(f"geo_fetch_total{{outcome=\"failure\"}} {_fetch_failure}")
        lines.append(f"geo_fetch_total{{outcome=\"fail_open\"}} {_fetch_fail_open}")
        for reason, count in sorted(_validation_failures.items()):
            lines.append(f'geo_validation_failures_total{{reason="{reason}"}} {count}')
        lines.append(f"geo_reload_success_total {_reload_success}")
        lines.append(f"geo_reload_failure_total {_reload_failure}")
        lines.append(f"geo_fail_open_events_total {_fail_open_events}")
    return "\n".join(lines) + "\n"
