"""
Validation: syntax (haproxy -c), size check, anchor check.
All must pass before activating a new map.
"""
import ipaddress
import logging
import os
import subprocess
from typing import List, Optional

from .config import ALLOWED_COUNTRY_CODES

logger = logging.getLogger(__name__)

# Size file to persist previous geo.map size
GEO_MAP_SIZE_FILE = "geo.map.size"


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
