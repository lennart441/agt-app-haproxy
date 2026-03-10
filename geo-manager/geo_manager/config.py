"""
Configuration from environment variables for Geo-Manager.
"""
import os
from dataclasses import dataclass
from typing import List, Optional

# Default erlaubte Länderkürzel (Geo-Whitelist), wenn GEO_ALLOWED_COUNTRIES nicht gesetzt.
DEFAULT_ALLOWED_COUNTRY_CODES = frozenset({"DE", "AT", "CH", "FR", "IT", "LU", "BE", "NL"})


@dataclass
class Config:
    """Runtime configuration from ENV."""

    node_name: str
    node_prio: int
    cluster_maxconn: int
    mesh_nodes: List[str]
    anchor_ips: List[str]
    allowed_country_codes: frozenset  # aus GEO_ALLOWED_COUNTRIES (kommasepariert), z. B. DE,AT,CH
    geo_source_url: str
    geo_source_ipv6_url: Optional[str]  # optional second CSV (same format as GEO_SOURCE_URL), merged into geo.map
    geo_blocks_ipv6_url: Optional[str]  # optional IPv6 blocks CSV (MaxMind format), merged when using GEO_BLOCKS_URL
    map_dir: str
    haproxy_cfg_path: str
    haproxy_socket: str
    stage_delay_prio2_hours: int
    stage_delay_prio3_hours: int
    fetch_interval_hours: float
    fetch_retries: int
    fetch_retry_delay_sec: float
    status_port: int
    size_deviation_threshold: float  # 0.9 = reject if new < 90% of old
    build_nice_level: int
    build_chunk_size: int
    build_sleep_after_chunk_ms: int
    # Mail (mailcow / SMTP)
    mail_enabled: bool
    mail_host: str
    mail_port: int
    mail_use_tls: bool
    mail_user: str
    mail_password: str
    mail_from: str
    mail_to: List[str]
    # Cluster health (weekly probe, latency, offline tracking)
    cluster_health_interval_hours: float
    cluster_health_timeout_sec: float
    # Fail-open: wenn Geo-Liste fehlt oder < N Einträge, alle durchlassen (mit Fehler-Log)
    fail_open_min_entries: int

    @classmethod
    def from_env(cls) -> "Config":
        mesh = os.environ.get("MESH_NODES", "")
        mesh_nodes = [s.strip() for s in mesh.split(",") if s.strip()]
        anchors = os.environ.get("ANCHOR_IPS", "")
        anchor_ips = [s.strip() for s in anchors.split(",") if s.strip()]

        raw_countries = os.environ.get("GEO_ALLOWED_COUNTRIES", "").strip()
        if raw_countries:
            allowed_country_codes = frozenset(
                c.strip().upper() for c in raw_countries.split(",") if c.strip()
            )
        else:
            allowed_country_codes = DEFAULT_ALLOWED_COUNTRY_CODES

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

        try:
            fetch_retries = int(os.environ.get("FETCH_RETRIES", "3"))
        except ValueError:
            fetch_retries = 3
        try:
            fetch_retry_delay = float(os.environ.get("FETCH_RETRY_DELAY_SEC", "60.0"))
        except ValueError:
            fetch_retry_delay = 60.0

        mail_enabled = os.environ.get("MAIL_ENABLED", "").strip().lower() in ("1", "true", "yes")
        mail_to_str = os.environ.get("MAIL_TO", "").strip()
        mail_to = [s.strip() for s in mail_to_str.split(",") if s.strip()]

        try:
            cluster_interval = float(os.environ.get("CLUSTER_HEALTH_INTERVAL_HOURS", "168.0"))  # weekly
        except ValueError:
            cluster_interval = 168.0
        try:
            cluster_timeout = float(os.environ.get("CLUSTER_HEALTH_TIMEOUT_SEC", "5.0"))
        except ValueError:
            cluster_timeout = 5.0

        try:
            cluster_maxconn = int(os.environ.get("CLUSTER_MAXCONN", "200"))
        except ValueError:
            cluster_maxconn = 200

        try:
            mail_port = int(os.environ.get("MAIL_PORT", "587"))
        except ValueError:
            mail_port = 587

        try:
            fail_open_min = int(os.environ.get("GEO_FAIL_OPEN_MIN_ENTRIES", "50"))
        except ValueError:
            fail_open_min = 50
        fail_open_min_entries = max(1, fail_open_min)

        return cls(
            node_name=os.environ.get("NODE_NAME", "agt-1"),
            node_prio=node_prio,
            cluster_maxconn=cluster_maxconn,
            mesh_nodes=mesh_nodes,
            anchor_ips=anchor_ips,
            allowed_country_codes=allowed_country_codes,
            geo_source_url=os.environ.get("GEO_SOURCE_URL", "").strip(),
            geo_source_ipv6_url=os.environ.get("GEO_SOURCE_IPV6_URL", "").strip() or None,
            geo_blocks_ipv6_url=os.environ.get("GEO_BLOCKS_IPV6_URL", "").strip() or None,
            map_dir=os.environ.get("MAP_DIR", "/usr/local/etc/haproxy/maps"),
            haproxy_cfg_path=os.environ.get(
                "HAPROXY_CFG_PATH", "/usr/local/etc/haproxy/haproxy.cfg"
            ),
            haproxy_socket=os.environ.get("HAPROXY_SOCKET", "/var/run/haproxy.sock"),
            stage_delay_prio2_hours=delay2,
            stage_delay_prio3_hours=delay3,
            fetch_interval_hours=fetch_interval,
            fetch_retries=max(1, fetch_retries),
            fetch_retry_delay_sec=max(1.0, fetch_retry_delay),
            status_port=status_port,
            size_deviation_threshold=threshold,
            build_nice_level=build_nice,
            build_chunk_size=build_chunk,
            build_sleep_after_chunk_ms=build_sleep_ms,
            mail_enabled=mail_enabled,
            mail_host=os.environ.get("MAIL_HOST", "").strip(),
            mail_port=mail_port,
            mail_use_tls=os.environ.get("MAIL_USE_TLS", "true").strip().lower() in ("1", "true", "yes"),
            mail_user=os.environ.get("MAIL_USER", "").strip(),
            mail_password=os.environ.get("MAIL_PASSWORD", "").strip(),
            mail_from=os.environ.get("MAIL_FROM", "").strip(),
            mail_to=mail_to,
            cluster_health_interval_hours=max(0.25, cluster_interval),
            cluster_health_timeout_sec=max(1.0, cluster_timeout),
            fail_open_min_entries=fail_open_min_entries,
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
