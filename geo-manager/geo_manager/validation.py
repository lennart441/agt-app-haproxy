"""
Validation: syntax (haproxy -c), size check, anchor check.
All must pass before activating a new map.
"""
import ipaddress
import logging
import os
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, List, Optional

from .config import DEFAULT_ALLOWED_COUNTRY_CODES

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

# Template strings in conf.d that are replaced by the HAProxy entrypoint;
# we need the same for haproxy -c in geo-manager.
PEER_LINE_1_TEMPLATE = "   server agt-1 __MESH_IP_1__:50000"
PEER_LINE_2_TEMPLATE = "   server agt-2 __MESH_IP_2__:50000"
PEER_LINE_3_TEMPLATE = "   server agt-3 __MESH_IP_3__:50000"

# In geo-manager container /etc/ssl/certs is the CA bundle (no haproxy.pem). For haproxy -c we substitute the cert path.
DEFAULT_HAPROXY_CRT_PATH = "/etc/ssl/certs/haproxy.pem"
ENV_HAPROXY_CRT_PATH_FOR_VALIDATION = "HAPROXY_CRT_PATH_FOR_VALIDATION"

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


def _apply_template_replacements(content: str, config: "Config") -> str:
    """Apply all template placeholder replacements (shared between file and directory processing)."""
    line1, line2, line3 = _build_peer_lines(config)
    content = content.replace("__NODE_NAME__", config.node_name)
    content = content.replace("__CLUSTER_MAXCONN__", str(config.cluster_maxconn))
    # Peer lines first (before general __MESH_IP_*__ replacement)
    content = content.replace(PEER_LINE_1_TEMPLATE, line1)
    content = content.replace(PEER_LINE_2_TEMPLATE, line2)
    content = content.replace(PEER_LINE_3_TEMPLATE, line3)
    # Backend server IPs from MESH_NODES
    default_ips = ("172.20.0.1", "172.20.0.2", "172.20.0.3")
    for idx in range(3):
        ip = config.mesh_nodes[idx] if idx < len(config.mesh_nodes) else default_ips[idx]
        content = content.replace(f"__MESH_IP_{idx + 1}__", ip)
    # Cert path for validation
    crt_path = os.environ.get(ENV_HAPROXY_CRT_PATH_FOR_VALIDATION, DEFAULT_HAPROXY_CRT_PATH)
    content = content.replace(DEFAULT_HAPROXY_CRT_PATH, crt_path)
    return content


def _get_processed_config_path(cfg_path: str, config: "Config") -> str:
    """
    Read template haproxy config, replace placeholders (same as HAProxy entrypoint).
    Supports both a single file and a conf.d directory.
    Caller must clean up the returned path (file or directory).
    """
    if os.path.isdir(cfg_path):
        tmp_dir = tempfile.mkdtemp(prefix="haproxy-confd-")
        for filename in sorted(os.listdir(cfg_path)):
            if not filename.endswith(".cfg"):
                continue
            with open(os.path.join(cfg_path, filename)) as f:
                content = f.read()
            content = _apply_template_replacements(content, config)
            with open(os.path.join(tmp_dir, filename), "w") as f:
                f.write(content)
        return tmp_dir
    with open(cfg_path) as f:
        content = f.read()
    content = _apply_template_replacements(content, config)
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
    Run haproxy -c -f haproxy_cfg_path. Config can be a single file or a conf.d directory.
    Returns True if config is valid.
    """
    if not os.path.exists(haproxy_cfg_path):
        logger.error("Config path not found: %s", haproxy_cfg_path)
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
    Like validate_syntax but replaces __NODE_NAME__, __CLUSTER_MAXCONN__, peer lines,
    __MESH_IP_*__ (same as HAProxy entrypoint) so haproxy -c sees a valid config.
    Supports both a single file and a conf.d directory.
    """
    if not os.path.exists(haproxy_cfg_path):
        logger.error("Config path not found: %s", haproxy_cfg_path)
        return False
    processed_path = _get_processed_config_path(haproxy_cfg_path, config)
    try:
        return validate_syntax(processed_path, map_dir, haproxy_bin)
    finally:
        try:
            if os.path.isdir(processed_path):
                shutil.rmtree(processed_path)
            else:
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


def count_geo_data_lines(geo_map_content: str) -> int:
    """
    Zählt Datenzeilen in geo map (Format: network\\tcountry).
    Leerzeilen und Zeilen die mit # starten zählen nicht.
    """
    count = 0
    for line in geo_map_content.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            count += 1
    return count


def build_permissive_geo_map(allowed_country_codes: frozenset) -> str:
    """
    Erzeugt eine permissive Geo-Map (alle IPs erlaubt) für Fail-Open.
    IPv4 und IPv6 werden auf ein erlaubtes Länderkürzel gemappt.
    """
    if not allowed_country_codes:
        code = "DE"
    else:
        code = sorted(allowed_country_codes)[0]
    return f"0.0.0.0/0\t{code}\n::/0\t{code}\n"


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
    allowed_codes: frozenset = DEFAULT_ALLOWED_COUNTRY_CODES,
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
