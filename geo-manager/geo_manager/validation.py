"""
Validation: syntax (haproxy -c), size check, anchor check.
All must pass before activating a new map.
"""
import ipaddress
import logging
import os
import subprocess
import tempfile
from typing import TYPE_CHECKING, List, Optional

from .config import ALLOWED_COUNTRY_CODES

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

# Template strings in haproxy.cfg that are replaced by the HAProxy entrypoint; we need the same for -c in geo-manager.
PEER_LINE_1_TEMPLATE = "   server agt-1 172.20.0.1:50000"
PEER_LINE_2_TEMPLATE = "   server agt-2 172.20.0.2:50000"
PEER_LINE_3_TEMPLATE = "   server agt-3 172.20.0.3:50000"

# Size file to persist previous geo.map size
GEO_MAP_SIZE_FILE = "geo.map.size"


def _build_peer_lines(config: "Config") -> tuple[str, str, str]:
    """Build peer server lines (same logic as haproxy-docker-entrypoint.sh)."""
    default_ips = ("172.20.0.1", "172.20.0.2", "172.20.0.3")
    lines = []
    for idx in range(1, 4):
        name = f"agt-{idx}"
        ip = config.mesh_nodes[idx - 1] if idx - 1 < len(config.mesh_nodes) else default_ips[idx - 1]
        if name == config.node_name:
            lines.append(f"   server {name}")
        else:
            lines.append(f"   server {name} {ip}:50000")
    return lines[0], lines[1], lines[2]


def _get_processed_config_path(cfg_path: str, config: "Config") -> str:
    """
    Read template haproxy.cfg, replace __NODE_NAME__, __CLUSTER_MAXCONN__, peer lines
    (same as HAProxy entrypoint), write to temp file. Caller must unlink the returned path.
    """
    with open(cfg_path) as f:
        content = f.read()
    line1, line2, line3 = _build_peer_lines(config)
    content = content.replace("__NODE_NAME__", config.node_name)
    content = content.replace("__CLUSTER_MAXCONN__", str(config.cluster_maxconn))
    content = content.replace(PEER_LINE_1_TEMPLATE, line1)
    content = content.replace(PEER_LINE_2_TEMPLATE, line2)
    content = content.replace(PEER_LINE_3_TEMPLATE, line3)
    fd, path = tempfile.mkstemp(suffix=".cfg", prefix="haproxy-")
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def validate_syntax(
    haproxy_cfg_path: str,
    map_dir: str,
    haproxy_bin: str = "haproxy",
) -> bool:
    """
    Run haproxy -c -f haproxy_cfg_path. Config must reference maps in map_dir.
    Returns True if config is valid.
    """
    if not os.path.isfile(haproxy_cfg_path):
        logger.error("Config file not found: %s", haproxy_cfg_path)
        return False
    try:
        result = subprocess.run(
            [haproxy_bin, "-c", "-f", haproxy_cfg_path],
            cwd=map_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("haproxy -c failed: %s", result.stderr)
            return False
        return True
    except FileNotFoundError:
        logger.error("haproxy binary not found: %s", haproxy_bin)
        return False
    except subprocess.TimeoutExpired:
        logger.error("haproxy -c timed out")
        return False


def validate_syntax_with_config(
    haproxy_cfg_path: str,
    map_dir: str,
    config: "Config",
    haproxy_bin: str = "haproxy",
) -> bool:
    """
    Like validate_syntax but replaces __NODE_NAME__, __CLUSTER_MAXCONN__, peer lines
    (same as HAProxy entrypoint) so haproxy -c sees a valid config. Use this when
    the config file is the repo template with placeholders.
    """
    if not os.path.isfile(haproxy_cfg_path):
        logger.error("Config file not found: %s", haproxy_cfg_path)
        return False
    processed_path = _get_processed_config_path(haproxy_cfg_path, config)
    try:
        return validate_syntax(processed_path, map_dir, haproxy_bin)
    finally:
        try:
            os.unlink(processed_path)
        except OSError:
            pass


def validate_size(
    new_content: str,
    map_dir: str,
    threshold: float,
    size_file: str = GEO_MAP_SIZE_FILE,
) -> bool:
    """
    New map must not be significantly smaller than the previous one.
    If no previous size is stored, accept any. Returns True if pass.
    """
    new_size = len(new_content.encode("utf-8"))
    size_path = os.path.join(map_dir, size_file)
    if not os.path.isfile(size_path):
        return True
    try:
        with open(size_path) as f:
            old_size = int(f.read().strip())
    except (ValueError, OSError):
        return True
    if old_size <= 0:
        return True
    ratio = new_size / old_size
    if ratio < threshold:
        logger.error(
            "Size check failed: new=%d old=%d ratio=%.2f threshold=%.2f",
            new_size,
            old_size,
            ratio,
            threshold,
        )
        return False
    return True


def _lookup_country_for_ip(geo_map_content: str, ip: str) -> Optional[str]:
    """
    Find country code for a single IP from geo map content.
    Map format: network\\tcountry per line. Longest matching prefix wins.
    """
    ip_obj = ipaddress.ip_address(ip)
    best_match: Optional[str] = None
    best_prefix = -1
    for line in geo_map_content.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        network_str, country = parts[0].strip(), parts[1].strip()
        try:
            network = ipaddress.ip_network(network_str, strict=False)
        except ValueError:
            continue
        if ip_obj in network and network.prefixlen > best_prefix:
            best_prefix = network.prefixlen
            best_match = country
    return best_match


def validate_anchors(
    geo_map_content: str,
    anchor_ips: List[str],
    allowed_codes: frozenset = ALLOWED_COUNTRY_CODES,
) -> bool:
    """
    Every anchor IP must resolve to an allowed country in the new map.
    Returns True if all anchors are allowed. If anchor_ips is empty, returns True (check skipped).
    """
    for ip in anchor_ips:
        ip = ip.strip()
        if not ip or ip.startswith("#"):
            continue
        country = _lookup_country_for_ip(geo_map_content, ip)
        if country is None:
            logger.warning("Anchor IP %s not found in map (will default to XX/blocked)", ip)
            return False
        if country.upper() not in allowed_codes:
            logger.error("Anchor IP %s has country %s (not in allowed %s)", ip, country, allowed_codes)
            return False
    return True


def persist_size(map_dir: str, size: int, size_file: str = GEO_MAP_SIZE_FILE) -> None:
    """Persist current geo map size for next size check."""
    size_path = os.path.join(map_dir, size_file)
    with open(size_path, "w") as f:
        f.write(str(size))
