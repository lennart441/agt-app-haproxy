"""
Geo-Manager: HTTP server for /geo/status and background loops for fetch/validate/staged rollout.
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from .config import Config, ALLOWED_COUNTRY_CODES
from .fetcher import (
    build_whitelist_map,
    fetch_geo_csv_to_map,
    fetch_geo_from_single_url,
    write_maps,
)
from .reload import trigger_reload
from .staging import get_master_validated_at, should_follower_activate
from .validation import persist_size, validate_anchors, validate_size, validate_syntax

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
    """Serves GET /geo/status with JSON: node_prio, validated_at, map_version."""

    def do_GET(self):
        if self.path != "/geo/status":
            self.send_error(404)
            return
        config = self.server.config  # type: ignore
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

    def log_message(self, format, *args):
        logger.debug("%s - %s", self.address_string(), format % args)


def run_master_loop(config: Config) -> None:
    """Master: periodically fetch, validate, activate."""
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
            _master_fetch_validate_activate(config)
        except Exception as e:
            logger.exception("Master loop error: %s", e)
        time.sleep(interval_sec)


def _master_fetch_validate_activate(config: Config) -> None:
    """Fetch from GEO_SOURCE_URL, validate, write maps, reload, set validated_at."""
    blocks_url = os.environ.get("GEO_BLOCKS_URL", "").strip()
    locations_url = os.environ.get("GEO_LOCATIONS_URL", "").strip()
    if blocks_url and locations_url:
        geo_content = fetch_geo_csv_to_map(
            blocks_url,
            locations_url,
            chunk_size=config.build_chunk_size,
            sleep_after_chunk_ms=config.build_sleep_after_chunk_ms,
        )
    else:
        geo_content = fetch_geo_from_single_url(
            config.geo_source_url,
            chunk_size=config.build_chunk_size,
            sleep_after_chunk_ms=config.build_sleep_after_chunk_ms,
        )

    if not geo_content.strip():
        logger.error("Fetched geo content is empty")
        return

    if not validate_size(geo_content, config.map_dir, config.size_deviation_threshold):
        logger.error("Size check failed; aborting")
        return
    if not validate_anchors(geo_content, config.anchor_ips, ALLOWED_COUNTRY_CODES):
        logger.error("Anchor check failed; aborting")
        return

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

    if not validate_syntax(config.haproxy_cfg_path, config.map_dir):
        logger.error("Syntax check failed; restoring backup")
        if os.path.isfile(backup_path):
            os.replace(backup_path, geo_map_path)
        return
    if os.path.isfile(backup_path):
        os.remove(backup_path)

    persist_size(config.map_dir, len(geo_content.encode("utf-8")))
    if trigger_reload(config.haproxy_socket):
        set_validated_at(datetime.now(timezone.utc))
        logger.info("Master: map activated and reloaded")
    else:
        logger.error("Reload failed; map was updated but HAProxy may not have picked it up")


def run_follower_loop(config: Config) -> None:
    """Follower: poll master's validated_at; when delay elapsed, fetch/validate/activate."""
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
            _master_fetch_validate_activate(config)
        except Exception as e:
            logger.exception("Follower loop error: %s", e)


def main() -> None:
    config = Config.from_env()
    server = HTTPServer(("0.0.0.0", config.status_port), GeoStatusHandler)
    server.config = config  # type: ignore

    if config.am_i_master():
        t = threading.Thread(target=run_master_loop, args=(config,), daemon=True)
    else:
        t = threading.Thread(target=run_follower_loop, args=(config,), daemon=True)
    t.start()

    logger.info(
        "Geo-Manager starting node_prio=%s status_port=%s",
        config.node_prio,
        config.status_port,
    )
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
