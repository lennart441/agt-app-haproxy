"""
Download Geo-IP data and convert to HAProxy map format.
Supports MaxMind GeoLite2 CSV (GeoLite2-Country-Blocks-IPv4.csv + GeoLite2-Country-Locations-en.csv).
"""
import csv
import io
import ipaddress
import logging
import os
import urllib.request
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def download_url(url: str, timeout: int = 60) -> bytes:
    """Download URL and return raw bytes."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_country_blocks_csv(content: bytes) -> List[Tuple[str, int]]:
    """
    Parse GeoLite2-Country-Blocks-IPv4.csv.
    Returns list of (network, geoname_id). network is in CIDR form.
    """
    rows: List[Tuple[str, int]] = []
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    for row in reader:
        network = (row.get("network") or "").strip()
        try:
            geoname_id = int(row.get("geoname_id") or row.get("registered_country_geoname_id") or "0")
        except ValueError:
            geoname_id = 0
        if network:
            rows.append((network, geoname_id))
    return rows


def parse_country_locations_csv(content: bytes) -> Dict[int, str]:
    """
    Parse GeoLite2-Country-Locations-en.csv (or similar).
    Returns dict geoname_id -> country_iso_code.
    """
    result: Dict[int, str] = {}
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    for row in reader:
        try:
            gid = int(row.get("geoname_id", 0))
        except ValueError:
            continue
        code = (row.get("country_iso_code") or row.get("locale_code") or "").strip().upper()
        if code and len(code) == 2:
            result[gid] = code
    return result


def build_geo_map(
    blocks: List[Tuple[str, int]],
    locations: Dict[int, str],
    default_country: str = "XX",
) -> str:
    """
    Build HAProxy map_ip content: one line per network "network\tcountry_code".
    Sorted by network for deterministic output. Locations not found get default_country.
    """
    lines: List[str] = []
    for network, geoname_id in blocks:
        country = locations.get(geoname_id, default_country)
        if not country or len(country) != 2:
            country = default_country
        lines.append(f"{network}\t{country}")
    lines.sort(key=_sort_key_network)
    return "\n".join(lines) + "\n" if lines else ""


def _sort_key_network(line: str) -> Tuple[int, ...]:
    """Sort key: parse first column as network for CIDR ordering."""
    part = line.split("\t", 1)[0].strip()
    try:
        net = ipaddress.ip_network(part, strict=False)
        return (int(net.network_address), int(net.netmask))
    except ValueError:
        return (0, 0)


def build_whitelist_map(anchor_ips: List[str]) -> str:
    """Build whitelist map content: one line per IP 'ip\t1' for map_ip -m found."""
    lines = []
    for ip in anchor_ips:
        ip = ip.strip()
        if ip and not ip.startswith("#"):
            lines.append(f"{ip}\t1")
    return "\n".join(lines) + "\n" if lines else ""


def fetch_geo_csv_to_map(
    blocks_url: str,
    locations_url: str,
    timeout: int = 60,
) -> str:
    """
    Fetch both CSVs from URLs and return HAProxy geo map content.
    blocks_url: GeoLite2-Country-Blocks-IPv4.csv
    locations_url: GeoLite2-Country-Locations-en.csv
    """
    blocks_content = download_url(blocks_url, timeout=timeout)
    locations_content = download_url(locations_url, timeout=timeout)
    blocks = parse_country_blocks_csv(blocks_content)
    locations = parse_country_locations_csv(locations_content)
    return build_geo_map(blocks, locations)


def fetch_geo_from_single_url(url: str, timeout: int = 60) -> str:
    """
    If GEO_SOURCE_URL points to a single CSV that already has network,country columns,
    use this. Otherwise use fetch_geo_csv_to_map with GEO_BLOCKS_URL and GEO_LOCATIONS_URL.
    """
    content = download_url(url, timeout=timeout)
    return _convert_simple_csv_to_map(content)


def _convert_simple_csv_to_map(content: bytes) -> str:
    """
    Convert a simple CSV with columns like network,country_code (or similar) to map.
    """
    rows: List[Tuple[str, str]] = []
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    for row in reader:
        network = (row.get("network") or row.get("network_cidr") or "").strip()
        country = (row.get("country_iso_code") or row.get("country") or row.get("country_code") or "XX").strip().upper()
        if len(country) != 2:
            country = "XX"
        if network:
            rows.append((network, country))
    lines = [f"{n}\t{c}" for n, c in rows]
    lines.sort(key=_sort_key_network)
    return "\n".join(lines) + "\n" if lines else ""


def write_maps(
    map_dir: str,
    geo_content: str,
    whitelist_content: str,
    geo_path: str = "geo.map",
    whitelist_path: str = "whitelist.map",
) -> None:
    """Write geo and whitelist map files to map_dir."""
    os.makedirs(map_dir, exist_ok=True)
    geo_file = os.path.join(map_dir, geo_path)
    whitelist_file = os.path.join(map_dir, whitelist_path)
    with open(geo_file, "w") as f:
        f.write(geo_content)
    with open(whitelist_file, "w") as f:
        f.write(whitelist_content)
    logger.info("Wrote %s and %s", geo_file, whitelist_file)
