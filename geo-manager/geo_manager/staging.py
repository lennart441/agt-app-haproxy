"""
Leader election and staged rollout logic.
Master (Prio 1) fetches and activates immediately; followers wait 48h/96h after master's validated_at.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import urllib.request

logger = logging.getLogger(__name__)


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
) -> bool:
    """
    Follower may activate only if master's validated_at is at least stage_delay_hours ago.
    my_prio 1: always True (master activates immediately, so we don't use this for master).
    """
    if my_prio == 1:
        return True
    if master_validated_at is None:
        return False
    if stage_delay_hours <= 0:
        return True
    now = datetime.now(timezone.utc)
    elapsed = (now - master_validated_at).total_seconds() / 3600.0
    return elapsed >= stage_delay_hours
