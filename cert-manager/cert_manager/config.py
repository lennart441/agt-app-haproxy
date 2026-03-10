"""
Configuration for cert-manager from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


@dataclass
class Config:
    node_name: str
    node_prio: int
    cert_is_master: bool
    mesh_nodes: List[str]

    status_port: int
    cluster_key: str

    source_fullchain: str
    source_privkey: str
    target_pem_path: str

    stage_delay_prio2_hours: int
    stage_delay_prio3_hours: int
    poll_interval_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        mesh_raw = os.environ.get("MESH_NODES", "")
        mesh_nodes = [s.strip() for s in mesh_raw.split(",") if s.strip()]

        def _int(env: str, default: int) -> int:
            try:
                return int(os.environ.get(env, str(default)))
            except ValueError:
                return default

        def _port(env: str, default: int) -> int:
            value = _int(env, default)
            if value <= 0 or value > 65535:
                return default
            return value

        node_prio = _int("NODE_PRIO", 1)

        status_port = _port("CERT_STATUS_PORT", 8081)
        stage_delay2 = _int("CERT_STAGE_DELAY_PRIO2_HOURS", 1)
        stage_delay3 = _int("CERT_STAGE_DELAY_PRIO3_HOURS", 2)
        poll_interval = _int("CERT_POLL_INTERVAL_SECONDS", 300)
        if poll_interval < 30:
            poll_interval = 30

        return cls(
            node_name=os.environ.get("NODE_NAME", "agt-1"),
            node_prio=node_prio,
            cert_is_master=os.environ.get("CERT_IS_MASTER", "").strip().lower()
            in ("1", "true", "yes"),
            mesh_nodes=mesh_nodes,
            status_port=status_port,
            cluster_key=os.environ.get("CERT_CLUSTER_KEY", "").strip(),
            source_fullchain=os.environ.get("CERT_SOURCE_FULLCHAIN", "").strip(),
            source_privkey=os.environ.get("CERT_SOURCE_PRIVKEY", "").strip(),
            target_pem_path=os.environ.get(
                "CERT_TARGET_PEM_PATH", "/etc/ssl/certs/haproxy.pem"
            ),
            stage_delay_prio2_hours=stage_delay2,
            stage_delay_prio3_hours=stage_delay3,
            poll_interval_seconds=poll_interval,
        )

    def stage_delay_hours_for_prio(self, prio: int) -> int:
        if prio == 1:
            return 0
        if prio == 2:
            return self.stage_delay_prio2_hours
        if prio == 3:
            return self.stage_delay_prio3_hours
        return max(self.stage_delay_prio2_hours, self.stage_delay_prio3_hours, prio * 2)

    def am_i_master(self) -> bool:
        return self.cert_is_master

