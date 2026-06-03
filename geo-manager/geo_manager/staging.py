"""
Leader election and staged rollout logic.
Master (Prio 1) fetches and activates immediately; followers wait 48h/96h after a newer master map
was first observed (soak timer does not reset on each daily master fetch).
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import urllib.request

logger = logging.getLogger(__name__)

_pending_master_validated_at: Optional[datetime] = None
_pending_lock = threading.Lock()


def reset_pending_master_validated_at() -> None:
    """Clear soak timer (tests, or after follower caught up with master)."""
    global _pending_master_validated_at
    with _pending_lock:
        _pending_master_validated_at = None


def get_master_status_url(mesh_node_ip: str, port: int = 8080) -> str:
    """Base URL for geo status on a mesh node."""
    return f"http://{mesh_node_ip}:{port}/geo/status"


def fetch_node_status(url: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """GET /geo/status and return parsed JSON or None."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("Failed to fetch %s: %s", url, e)
        return None


def get_master_validated_at(
    mesh_nodes: List[str],
    status_port: int,
    timeout: float = 5.0,
) -> Optional[Tuple[str, Optional[datetime]]]:
    """
    Query mesh nodes and return (master_node_ip, validated_at) for the master (node_prio=1).
    If no master is reachable, return None.
    validated_at is None if master has never activated a map.
    """
    for ip in mesh_nodes:
        url = get_master_status_url(ip, status_port)
        data = fetch_node_status(url, timeout=timeout)
        if data is None:
            continue
        prio = data.get("node_prio")
        if prio == 1:
            raw = data.get("validated_at")
            if raw:
                try:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return (ip, dt)
                except (ValueError, TypeError):
                    pass
            return (ip, None)
    return None


def should_follower_activate(
    my_prio: int,
    master_validated_at: Optional[datetime],
    stage_delay_hours: int,
    *,
    local_validated_at: Optional[datetime] = None,
) -> bool:
    """
    Follower may activate when master has a newer map and that gap has soaked stage_delay_hours.

    Bootstrap: no local map yet → adopt immediately when master has data.

    Update mode: record the first master_validated_at seen while local is behind; wait
    stage_delay_hours from that timestamp. Do not reset the timer when the master publishes
    daily updates (otherwise a 24h fetch interval with 48h delay never elapses).
    """
    if my_prio == 1:
        return True
    if master_validated_at is None:
        return False
    if local_validated_at is None:
        reset_pending_master_validated_at()
        return True
    if stage_delay_hours <= 0:
        reset_pending_master_validated_at()
        return True

    master_at = master_validated_at
    if master_at.tzinfo is None:
        master_at = master_at.replace(tzinfo=timezone.utc)
    local_at = local_validated_at
    if local_at.tzinfo is None:
        local_at = local_at.replace(tzinfo=timezone.utc)

    if local_at >= master_at:
        reset_pending_master_validated_at()
        return False

    global _pending_master_validated_at
    with _pending_lock:
        if _pending_master_validated_at is None:
            _pending_master_validated_at = master_at
        soak_anchor = _pending_master_validated_at

    now = datetime.now(timezone.utc)
    elapsed = (now - soak_anchor).total_seconds() / 3600.0
    if elapsed >= stage_delay_hours:
        return True
    logger.debug(
        "Follower staged rollout: %.1fh / %dh since first newer master map (%s)",
        elapsed,
        stage_delay_hours,
        soak_anchor.isoformat(),
    )
    return False
