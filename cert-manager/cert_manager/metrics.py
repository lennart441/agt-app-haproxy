"""
Prometheus metrics for cert-manager: deploy outcomes, follower sync, node identity.
Thread-safe counters exposed via /metrics endpoint.
"""

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .state import CertState

_lock = threading.Lock()
_deploy_success = 0
_deploy_failure = 0
_follower_sync_success = 0
_follower_sync_failure = 0


def inc_deploy_success() -> None:
    with _lock:
        global _deploy_success
        _deploy_success += 1


def inc_deploy_failure() -> None:
    with _lock:
        global _deploy_failure
        _deploy_failure += 1


def inc_follower_sync_success() -> None:
    with _lock:
        global _follower_sync_success
        _follower_sync_success += 1


def inc_follower_sync_failure() -> None:
    with _lock:
        global _follower_sync_failure
        _follower_sync_failure += 1


def reset_for_tests() -> None:
    """Reset all counters. For use in tests only."""
    with _lock:
        global _deploy_success, _deploy_failure
        global _follower_sync_success, _follower_sync_failure
        _deploy_success = 0
        _deploy_failure = 0
        _follower_sync_success = 0
        _follower_sync_failure = 0


def to_prometheus(config: "Config", state: "CertState | None") -> str:
    """Return Prometheus text format for cert-manager metrics."""
    lines: list[str] = []
    with _lock:
        lines.append(f"cert_node_prio {config.node_prio}")
        lines.append(f"cert_is_master {1 if config.am_i_master() else 0}")
        ts = int(state.validated_since.timestamp()) if state else 0
        lines.append(f"cert_validated_since_timestamp_seconds {ts}")
        lines.append(f'cert_deploy_total{{outcome="success"}} {_deploy_success}')
        lines.append(f'cert_deploy_total{{outcome="failure"}} {_deploy_failure}')
        lines.append(
            f'cert_follower_sync_total{{outcome="success"}} {_follower_sync_success}'
        )
        lines.append(
            f'cert_follower_sync_total{{outcome="failure"}} {_follower_sync_failure}'
        )
    return "\n".join(lines) + "\n"
