"""
Follower logic: poll master for certificate version and download PEM when due.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from .config import Config
from .leader import write_target_pem
from .state import CertState, compute_version, get_state, set_state_from_pem

logger = logging.getLogger(__name__)


def _http_get(host: str, port: int, path: str, timeout: float = 5.0) -> Tuple[int, bytes]:  # pragma: no cover
    """Very small HTTP GET client using stdlib only (no TLS, mesh is protected by WireGuard)."""
    import http.client

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body
    finally:
        conn.close()


def _parse_iso8601(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def get_master_status(config: Config) -> Optional[Tuple[str, CertState]]:
    """
    Query all mesh nodes for a master cert status.

    Exactly one node must report itself as cert master. If none or more than one
    master is found, return None and let callers treat the cluster as disabled.
    """
    masters: list[Tuple[str, CertState]] = []
    for node_ip in config.mesh_nodes:
        try:
            path = "/cert/status"
            if config.cluster_key:
                path = f"/cert/status?cluster_key={config.cluster_key}"
            status, body = _http_get(node_ip, config.status_port, path)
        except Exception as exc:
            logger.debug("Error querying %s: %s", node_ip, exc)
            continue
        if status != 200:
            continue
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            continue
        if not data.get("cert_is_master"):
            continue
        version = data.get("version")
        validated_since = data.get("validated_since")
        if not version or not validated_since:
            continue
        ts = _parse_iso8601(validated_since)
        if ts is None:
            continue
        state = CertState(version=version, validated_since=ts)
        masters.append((node_ip, state))

    if not masters:
        return None
    if len(masters) > 1:
        logger.error(
            "Multiple cert masters detected (%s nodes). Disabling rollout.",
            len(masters),
        )
        return None
    return masters[0]


def should_activate(
    config: Config,
    master_state: CertState,
    now: Optional[datetime] = None,
) -> bool:
    """
    Decide if follower should activate master's version based on delay per prio.
    """
    if config.am_i_master():
        return False
    delay_hours = config.stage_delay_hours_for_prio(config.node_prio)
    if delay_hours <= 0:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    delta = now - master_state.validated_since
    return delta.total_seconds() >= delay_hours * 3600


def download_cert_from_master(
    master_ip: str,
    config: Config,
    version: str,
) -> Optional[bytes]:
    """
    Download PEM from master and perform simple integrity check (version hash).
    """
    try:
        path = f"/cert/download?version={version}"
        if config.cluster_key:
            path = f"{path}&cluster_key={config.cluster_key}"
        status, body = _http_get(
            master_ip,
            config.status_port,
            path,
        )
    except Exception as exc:
        logger.warning("Error downloading cert from %s: %s", master_ip, exc)
        return None
    if status != 200:
        logger.warning("Download from %s failed with HTTP %s", master_ip, status)
        return None
    if compute_version(body) != version:
        logger.warning("Version hash mismatch from %s", master_ip)
        return None
    return body


def run_follower_once(config: Config) -> bool:
    """
    Single follower iteration: try to fetch PEM from master if due.
    Returns True if a cert was activated, False otherwise.

    Bootstrap: If no local PEM exists yet, fetch immediately from master
    (no staged delay). Staged delay applies only to updates (new version).
    """
    if config.am_i_master() or not config.mesh_nodes:
        return False
    result = get_master_status(config)
    if result is None:
        return False
    master_ip, master_state = result
    need_bootstrap = not os.path.exists(config.target_pem_path)
    if not need_bootstrap and not should_activate(config, master_state):
        return False
    pem = download_cert_from_master(master_ip, config, master_state.version)
    if pem is None:
        return False
    write_target_pem(config, pem)
    set_state_from_pem(pem)
    logger.info(
        "Follower activated certificate version %s from %s",
        master_state.version,
        master_ip,
    )
    return True


def run_follower_loop(config: Config) -> None:  # pragma: no cover
    """Background loop for followers: poll master and activate new certs when due.

    Diese Funktion bildet vor allem die Orchestrierung bereits separat getesteter
    Helferfunktionen ab (_http_get, get_master_status, should_activate,
    download_cert_from_master, write_target_pem, set_state_from_pem) und enthält
    eine Endlosschleife. Eine vollständige Abdeckung erfolgt daher über
    Integrationstests im Betrieb; in den Unit-Tests wird sie bewusst von der
    Coverage ausgenommen.
    """
    if config.am_i_master():
        return
    if not config.mesh_nodes:
        logger.info("No MESH_NODES configured; follower loop disabled.")
        return
    while True:
        time.sleep(config.poll_interval_seconds)
        try:
            result = get_master_status(config)
            if result is None:
                continue
            master_ip, master_state = result
            local = get_state()
            if local is not None and local.version == master_state.version:
                continue
            if not should_activate(config, master_state):
                continue
            pem = download_cert_from_master(master_ip, config, master_state.version)
            if pem is None:
                continue
            write_target_pem(config, pem)
            set_state_from_pem(pem)
            logger.info(
                "Follower activated new certificate version %s from %s",
                master_state.version,
                master_ip,
            )
        except Exception as exc:
            logger.exception("Follower loop error: %s", exc)

