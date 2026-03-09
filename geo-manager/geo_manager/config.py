"""
Configuration from environment variables for Geo-Manager.
"""
import os
from dataclasses import dataclass
from typing import List


# Allowed country codes for geo (DE, EU border regions). Anchor IPs must resolve to these.
ALLOWED_COUNTRY_CODES = frozenset({"DE", "AT", "CH", "FR", "IT", "LU", "BE", "NL"})


@dataclass
class Config:
    """Runtime configuration from ENV."""

    node_name: str
    node_prio: int
    mesh_nodes: List[str]
    anchor_ips: List[str]
    geo_source_url: str
    map_dir: str
    haproxy_cfg_path: str
    haproxy_socket: str
    stage_delay_prio2_hours: int
    stage_delay_prio3_hours: int
    fetch_interval_hours: float
    status_port: int
    size_deviation_threshold: float  # 0.9 = reject if new < 90% of old
    build_nice_level: int  # process nice (e.g. 10 = lower CPU priority)
    build_chunk_size: int  # process N lines/blocks per chunk, then sleep
    build_sleep_after_chunk_ms: int  # ms to sleep after each chunk to yield CPU

    @classmethod
    def from_env(cls) -> "Config":
        mesh = os.environ.get("MESH_NODES", "")
        mesh_nodes = [s.strip() for s in mesh.split(",") if s.strip()]
        anchors = os.environ.get("ANCHOR_IPS", "")
        anchor_ips = [s.strip() for s in anchors.split(",") if s.strip()]

        try:
            node_prio = int(os.environ.get("NODE_PRIO", "1"))
        except ValueError:
            node_prio = 1

        try:
            delay2 = int(os.environ.get("STAGE_DELAY_PRIO2_HOURS", "48"))
        except ValueError:
            delay2 = 48
        try:
            delay3 = int(os.environ.get("STAGE_DELAY_PRIO3_HOURS", "96"))
        except ValueError:
            delay3 = 96

        try:
            fetch_interval = float(os.environ.get("FETCH_INTERVAL_HOURS", "24.0"))
        except ValueError:
            fetch_interval = 24.0

        try:
            threshold = float(os.environ.get("SIZE_DEVIATION_THRESHOLD", "0.9"))
        except ValueError:
            threshold = 0.9

        try:
            status_port = int(os.environ.get("GEO_STATUS_PORT", "8080"))
        except ValueError:
            status_port = 8080

        try:
            build_nice = int(os.environ.get("BUILD_NICE_LEVEL", "10"))
        except ValueError:
            build_nice = 10
        try:
            build_chunk = int(os.environ.get("BUILD_CHUNK_SIZE", "5000"))
        except ValueError:
            build_chunk = 5000
        try:
            build_sleep_ms = int(os.environ.get("BUILD_SLEEP_AFTER_CHUNK_MS", "50"))
        except ValueError:
            build_sleep_ms = 50

        return cls(
            node_name=os.environ.get("NODE_NAME", "agt-1"),
            node_prio=node_prio,
            mesh_nodes=mesh_nodes,
            anchor_ips=anchor_ips,
            geo_source_url=os.environ.get("GEO_SOURCE_URL", "").strip(),
            map_dir=os.environ.get("MAP_DIR", "/usr/local/etc/haproxy/maps"),
            haproxy_cfg_path=os.environ.get(
                "HAPROXY_CFG_PATH", "/usr/local/etc/haproxy/haproxy.cfg"
            ),
            haproxy_socket=os.environ.get("HAPROXY_SOCKET", "/var/run/haproxy.sock"),
            stage_delay_prio2_hours=delay2,
            stage_delay_prio3_hours=delay3,
            fetch_interval_hours=fetch_interval,
            status_port=status_port,
            size_deviation_threshold=threshold,
            build_nice_level=build_nice,
            build_chunk_size=build_chunk,
            build_sleep_after_chunk_ms=build_sleep_ms,
        )

    def stage_delay_hours_for_prio(self, prio: int) -> int:
        if prio == 1:
            return 0
        if prio == 2:
            return self.stage_delay_prio2_hours
        if prio == 3:
            return self.stage_delay_prio3_hours
        return max(
            self.stage_delay_prio2_hours,
            self.stage_delay_prio3_hours,
            prio * 24,
        )

    def am_i_master(self) -> bool:
        return self.node_prio == 1
