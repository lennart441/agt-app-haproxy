"""
Cluster health: probe mesh nodes, record latency and offline periods.
Updated periodically (e.g. weekly). Exposes JSON and Prometheus metrics.
"""
import json
import logging
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# Keep last N probe results per node for offline-duration and stability
MAX_HISTORY_PER_NODE = 168  # e.g. 1/week for 24 weeks or 24/h for 1 week


@dataclass
class NodeProbeResult:
    """Single probe result for one node."""
    node_ip: str
    at: str  # ISO timestamp
    reachable: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class ClusterHealthState:
    """Mutable state for cluster health; thread-safe via lock."""
    last_probe_at: Optional[datetime] = None
    results: List[NodeProbeResult] = field(default_factory=list)
    history_per_node: Dict[str, Deque[NodeProbeResult]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(
        self,
        results_list: List[NodeProbeResult],
    ) -> None:
        with self._lock:
            self.last_probe_at = datetime.now(timezone.utc)
            self.results = list(results_list)
            for r in results_list:
                if r.node_ip not in self.history_per_node:
                    self.history_per_node[r.node_ip] = deque(maxlen=MAX_HISTORY_PER_NODE)
                self.history_per_node[r.node_ip].append(r)

    def to_json_dict(self) -> Dict[str, Any]:
        with self._lock:
            last = self.last_probe_at.isoformat() if self.last_probe_at else None
            nodes = []
            for r in self.results:
                nodes.append({
                    "node_ip": r.node_ip,
                    "reachable": r.reachable,
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                    "at": r.at,
                })
            # Offline summary: for each node, last failure run length and total failures in history
            offline_summary: Dict[str, Dict[str, Any]] = {}
            for ip, deq in self.history_per_node.items():
                items = list(deq)
                if not items:
                    continue
                failures = sum(1 for x in items if not x.reachable)
                # Consecutive failures at end
                consec = 0
                for x in reversed(items):
                    if not x.reachable:
                        consec += 1
                    else:
                        break
                offline_summary[ip] = {
                    "total_probes": len(items),
                    "failures": failures,
                    "consecutive_failures": consec,
                }
            return {
                "last_probe_at": last,
                "nodes": nodes,
                "offline_summary": offline_summary,
            }

    def to_prometheus(self) -> str:
        lines: List[str] = []
        with self._lock:
            for r in self.results:
                reachable = 1 if r.reachable else 0
                lines.append(
                    f'geo_cluster_node_reachable{{node_ip="{r.node_ip}"}} {reachable}'
                )
                if r.latency_ms is not None:
                    lines.append(
                        f'geo_cluster_node_latency_ms{{node_ip="{r.node_ip}"}} {r.latency_ms}'
                    )
            if self.last_probe_at:
                ts = self.last_probe_at.timestamp()
                lines.append(f"geo_cluster_last_probe_timestamp {int(ts)}")
        return "\n".join(lines) + "\n" if lines else ""


_state: Optional[ClusterHealthState] = None
_state_lock = threading.Lock()


def get_cluster_health_state() -> Optional[ClusterHealthState]:
    global _state
    with _state_lock:
        return _state


def set_cluster_health_state(state: ClusterHealthState) -> None:
    global _state
    with _state_lock:
        _state = state


def probe_node(
    node_ip: str,
    port: int,
    timeout: float,
) -> NodeProbeResult:
    """Probe one node (GET /geo/status), return result with latency."""
    url = f"http://{node_ip}:{port}/geo/status"
    start = time.monotonic()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        latency_ms = (time.monotonic() - start) * 1000.0
        return NodeProbeResult(
            node_ip=node_ip,
            at=datetime.now(timezone.utc).isoformat(),
            reachable=True,
            latency_ms=round(latency_ms, 2),
        )
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000.0
        return NodeProbeResult(
            node_ip=node_ip,
            at=datetime.now(timezone.utc).isoformat(),
            reachable=False,
            latency_ms=round(latency_ms, 2),
            error=str(e),
        )


def run_cluster_probe(
    mesh_nodes: List[str],
    port: int,
    timeout: float,
) -> List[NodeProbeResult]:
    """Probe all mesh nodes; never raises."""
    results: List[NodeProbeResult] = []
    for ip in mesh_nodes:
        ip = ip.strip()
        if not ip:
            continue
        try:
            results.append(probe_node(ip, port, timeout))
        except Exception as e:
            logger.warning("Probe %s failed: %s", ip, e)
            results.append(
                NodeProbeResult(
                    node_ip=ip,
                    at=datetime.now(timezone.utc).isoformat(),
                    reachable=False,
                    error=str(e),
                )
            )
    return results


def update_and_get_json(mesh_nodes: List[str], port: int, timeout: float) -> str:
    """Run probe, update state, return JSON string."""
    results = run_cluster_probe(mesh_nodes, port, timeout)
    state = get_cluster_health_state()
    if state is None:
        state = ClusterHealthState()
        set_cluster_health_state(state)
    state.update(results)
    return json.dumps(state.to_json_dict(), indent=2)


def get_metrics_prometheus() -> str:
    """Return current state as Prometheus text format."""
    state = get_cluster_health_state()
    if state is None:
        return ""
    return state.to_prometheus()
