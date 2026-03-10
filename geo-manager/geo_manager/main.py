"""
Geo-Manager: HTTP server for /geo/status, /health, /metrics, /cluster and background loops.
"""
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from .cluster_health import (
    ClusterHealthState,
    get_cluster_health_state,
    run_cluster_probe,
    set_cluster_health_state,
)
from .config import Config
from .fetcher import (
    build_whitelist_map,
    fetch_geo_csv_to_map,
    fetch_geo_from_single_url,
    merge_geo_map_contents,
    write_maps,
)
from .notify import send_failure_mail
from .reload import trigger_reload
from .staging import get_master_validated_at, should_follower_activate
from .validation import (
    build_permissive_geo_map,
    count_geo_data_lines,
    persist_size,
    validate_anchors,
    validate_size,
    validate_syntax_with_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Global state for /geo/status
_validated_at: Optional[datetime] = None
_validated_at_lock = threading.Lock()


def set_validated_at(dt: datetime) -> None:
    with _validated_at_lock:
        global _validated_at
        _validated_at = dt


def get_validated_at() -> Optional[datetime]:
    with _validated_at_lock:
        return _validated_at


class GeoStatusHandler(BaseHTTPRequestHandler):
    """Serves GET /geo/status, /health, /metrics, /cluster and POST /geo/deploy-now."""

    def do_GET(self):
        path = self.path.split("?")[0]
        config = self.server.config  # type: ignore
        if path == "/health":
            self._send_health()
            return
        if path == "/metrics":
            self._send_metrics()
            return
        if path == "/cluster":
            self._send_cluster(config)
            return
        if path == "/geo/status":
            self._send_geo_status(config)
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        config = self.server.config  # type: ignore
        if path == "/geo/deploy-now":
            self._handle_geo_deploy_now(config)
            return
        self.send_error(404)

    def _send_health(self) -> None:
        """GET /health: simple 200 OK for load balancer / monitoring."""
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_metrics(self) -> None:
        """GET /metrics: Prometheus text format (cluster health + optional geo)."""
        state = get_cluster_health_state()
        if state is not None:
            body = state.to_prometheus().encode("utf-8")
        else:
            body = b""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cluster(self, config: Config) -> None:
        """GET /cluster: JSON cluster health (last probe result, latency, offline summary)."""
        state = get_cluster_health_state()
        if state is None:
            body = json.dumps({"last_probe_at": None, "nodes": [], "offline_summary": {}}).encode("utf-8")
        else:
            body = json.dumps(state.to_json_dict()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_geo_status(self, config: Config) -> None:
        """GET /geo/status: node_prio, validated_at, map_version."""
        validated = get_validated_at()
        payload = {
            "node_prio": config.node_prio,
            "node_name": config.node_name,
            "validated_at": validated.isoformat() if validated else None,
            "map_version": validated.isoformat() if validated else None,
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_geo_deploy_now(self, config: Config) -> None:
        """POST /geo/deploy-now: manually trigger fetch/validate/activate on master."""
        if not config.am_i_master():
            self.send_error(403, "Only geo-master may deploy maps")
            return
        try:
            _master_fetch_validate_activate(config)
        except Exception as exc:
            msg = f"Geo deploy failed: {exc}".encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        logger.debug("%s - %s", self.address_string(), format % args)


def run_master_loop(config: Config) -> None:
    """Master: periodically fetch, validate, activate. Retries on failure; mail on final failure."""
    try:
        os.nice(config.build_nice_level)
    except (AttributeError, OSError):
        pass
    if not config.geo_source_url:
        logger.warning("GEO_SOURCE_URL not set; master will not fetch")
        return
    interval_sec = config.fetch_interval_hours * 3600
    while True:
        try:
            success = False
            last_error: Optional[str] = None
            for attempt in range(config.fetch_retries):
                try:
                    _master_fetch_validate_activate(config)
                    success = True
                    break
                except Exception as e:
                    last_error = str(e)
                    logger.warning(
                        "Master fetch attempt %s/%s failed: %s",
                        attempt + 1,
                        config.fetch_retries,
                        e,
                    )
                    if attempt < config.fetch_retries - 1:
                        time.sleep(config.fetch_retry_delay_sec)
            if not success and last_error:
                _notify_fetch_failure(config, last_error)
        except Exception as e:
            logger.exception("Master loop error: %s", e)
        time.sleep(interval_sec)


def _notify_fetch_failure(config: Config, error_detail: str) -> None:
    """Send failure mail after all retries; never raise."""
    try:
        subject = f"[Geo-Manager] Fehlschlag nach {config.fetch_retries} Versuchen – {config.node_name}"
        body = (
            f"Knoten: {config.node_name} (Prio {config.node_prio})\n"
            f"Fehler: {error_detail}\n"
            f"Die IP-Listen wurden nicht aktualisiert. Bitte prüfen."
        )
        send_failure_mail(config, subject, body)
    except Exception as e:
        logger.warning("Could not send failure mail: %s", e)


def _master_fetch_validate_activate(config: Config) -> None:
    """Fetch from GEO_SOURCE_URL, validate, write maps, reload, set validated_at. Raises on failure."""
    blocks_url = os.environ.get("GEO_BLOCKS_URL", "").strip()
    locations_url = os.environ.get("GEO_LOCATIONS_URL", "").strip()
    retries = config.fetch_retries
    retry_delay = config.fetch_retry_delay_sec
    if blocks_url and locations_url:
        geo_content = fetch_geo_csv_to_map(
            blocks_url,
            locations_url,
            blocks_ipv6_url=config.geo_blocks_ipv6_url,
            retries=retries,
            retry_delay_sec=retry_delay,
            chunk_size=config.build_chunk_size,
            sleep_after_chunk_ms=config.build_sleep_after_chunk_ms,
        )
    else:
        geo_content = fetch_geo_from_single_url(
            config.geo_source_url,
            retries=retries,
            retry_delay_sec=retry_delay,
            chunk_size=config.build_chunk_size,
            sleep_after_chunk_ms=config.build_sleep_after_chunk_ms,
        )
        if config.geo_source_ipv6_url:
            geo_ipv6 = fetch_geo_from_single_url(
                config.geo_source_ipv6_url,
                retries=retries,
                retry_delay_sec=retry_delay,
                chunk_size=config.build_chunk_size,
                sleep_after_chunk_ms=config.build_sleep_after_chunk_ms,
            )
            if geo_ipv6.strip():
                geo_content = merge_geo_map_contents(geo_content, geo_ipv6)

    # Fail-open: Liste fehlt oder zu klein (< fail_open_min_entries) → alle durchlassen, aber Fehler loggen
    fail_open = False
    if not geo_content.strip():
        fail_open = True
        logger.error(
            "Fail-open: Geo-Liste fehlt (leer). Erlaube alle Zugriffe; bitte GEO_SOURCE_URL prüfen."
        )
        geo_content = build_permissive_geo_map(config.allowed_country_codes)
    elif count_geo_data_lines(geo_content) < config.fail_open_min_entries:
        fail_open = True
        n = count_geo_data_lines(geo_content)
        logger.error(
            "Fail-open: Geo-Liste hat nur %d Einträge (Minimum %d). Erlaube alle Zugriffe.",
            n,
            config.fail_open_min_entries,
        )
        geo_content = build_permissive_geo_map(config.allowed_country_codes)

    if not fail_open and not validate_size(
        geo_content, config.map_dir, config.size_deviation_threshold
    ):
        raise RuntimeError("Size check failed")

    # Plausibilitätscheck (Anchor-Check): nur wenn ANCHOR_IPS gesetzt; leer = Check überspringen, nicht abbrechen
    if config.anchor_ips:
        if not validate_anchors(
            geo_content, config.anchor_ips, config.allowed_country_codes
        ):
            raise RuntimeError("Anchor check failed")
    else:
        logger.info("ANCHOR_IPS empty: anchor plausibility check skipped")

    geo_map_path = os.path.join(config.map_dir, "geo.map")
    backup_path = os.path.join(config.map_dir, "geo.map.bak")
    whitelist_content = build_whitelist_map(config.anchor_ips)

    if os.path.isfile(geo_map_path):
        os.replace(geo_map_path, backup_path)
    write_maps(
        config.map_dir,
        geo_content,
        whitelist_content,
        chunk_size=config.build_chunk_size,
        sleep_after_chunk_ms=config.build_sleep_after_chunk_ms,
    )

    if not validate_syntax_with_config(
        config.haproxy_cfg_path, config.map_dir, config
    ):
        if os.path.isfile(backup_path):
            os.replace(backup_path, geo_map_path)
        raise RuntimeError("Syntax check failed; backup restored")

    if os.path.isfile(backup_path):
        os.remove(backup_path)

    persist_size(config.map_dir, len(geo_content.encode("utf-8")))
    if not trigger_reload(config.haproxy_socket):
        raise RuntimeError("HAProxy reload failed")
    set_validated_at(datetime.now(timezone.utc))
    logger.info("Master: map activated and reloaded")


def run_follower_loop(config: Config) -> None:
    """Follower: poll master's validated_at; when delay elapsed, fetch/validate/activate with retries."""
    try:
        os.nice(config.build_nice_level)
    except (AttributeError, OSError):
        pass
    if config.node_prio == 1:
        return
    delay_hours = config.stage_delay_hours_for_prio(config.node_prio)
    poll_interval_sec = 3600
    while True:
        time.sleep(poll_interval_sec)
        try:
            result = get_master_validated_at(
                config.mesh_nodes, config.status_port
            )
            if result is None:
                continue
            _master_ip, master_validated_at = result
            if not should_follower_activate(
                config.node_prio, master_validated_at, delay_hours
            ):
                continue
            success = False
            last_error: Optional[str] = None
            for attempt in range(config.fetch_retries):
                try:
                    _master_fetch_validate_activate(config)
                    success = True
                    break
                except Exception as e:
                    last_error = str(e)
                    if attempt < config.fetch_retries - 1:
                        time.sleep(config.fetch_retry_delay_sec)
            if not success and last_error:
                _notify_fetch_failure(config, last_error)
        except Exception as e:
            logger.exception("Follower loop error: %s", e)


def run_cluster_health_loop(config: Config) -> None:
    """Background: periodically probe mesh nodes, update cluster health state."""
    if not config.mesh_nodes:
        return
    interval_sec = config.cluster_health_interval_hours * 3600
    state = ClusterHealthState()
    set_cluster_health_state(state)
    while True:
        try:
            results = run_cluster_probe(
                config.mesh_nodes,
                config.status_port,
                config.cluster_health_timeout_sec,
            )
            state.update(results)
            time.sleep(interval_sec)
        except Exception as e:
            logger.warning("Cluster health probe error: %s", e)
            time.sleep(interval_sec)


def main() -> None:
    config = Config.from_env()
    server = HTTPServer(("0.0.0.0", config.status_port), GeoStatusHandler)
    server.config = config  # type: ignore

    def _shutdown(_signum=None, _frame=None) -> None:
        logger.info("Received signal, shutting down gracefully")
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if config.am_i_master():
        t = threading.Thread(target=run_master_loop, args=(config,), daemon=True)
    else:
        t = threading.Thread(target=run_follower_loop, args=(config,), daemon=True)
    t.start()

    if config.mesh_nodes:
        t_cluster = threading.Thread(
            target=run_cluster_health_loop,
            args=(config,),
            daemon=True,
        )
        t_cluster.start()

    logger.info(
        "Geo-Manager starting node_prio=%s status_port=%s",
        config.node_prio,
        config.status_port,
    )
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
