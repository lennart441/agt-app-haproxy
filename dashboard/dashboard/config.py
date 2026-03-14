"""Configuration for dashboard from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    node_name: str
    node_prio: int
    mesh_nodes: List[str]
    geo_status_port: int
    cert_status_port: int
    cert_cluster_key: str
    stats_user: str
    stats_password: str
    dashboard_port: int
    docker_socket: str
    # Container names to expose in the log viewer (ordered)
    container_names: List[str] = field(default_factory=lambda: [
        "haproxy_gateway", "geo-manager", "cert-manager", "coraza-spoa", "dashboard",
    ])

    @classmethod
    def from_env(cls) -> Config:
        mesh_raw = os.environ.get("MESH_NODES", "")
        mesh_nodes = [s.strip() for s in mesh_raw.split(",") if s.strip()]

        def _int(env: str, default: int) -> int:
            try:
                return int(os.environ.get(env, str(default)))
            except ValueError:
                return default

        return cls(
            node_name=os.environ.get("NODE_NAME", "agt-1"),
            node_prio=_int("NODE_PRIO", 1),
            mesh_nodes=mesh_nodes,
            geo_status_port=_int("GEO_STATUS_PORT", 8080),
            cert_status_port=_int("CERT_STATUS_PORT", 8081),
            cert_cluster_key=os.environ.get("CERT_CLUSTER_KEY", "").strip(),
            stats_user=os.environ.get("STATS_USER", "admin"),
            stats_password=os.environ.get("STATS_PASSWORD", ""),
            dashboard_port=_int("DASHBOARD_PORT", 8082),
            docker_socket=os.environ.get(
                "DOCKER_SOCKET", "/var/run/docker.sock"
            ),
        )
