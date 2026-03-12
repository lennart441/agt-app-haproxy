"""
Download Geo-IP data and convert to HAProxy map format.
Supports MaxMind GeoLite2 CSV (GeoLite2-Country-Blocks-IPv4.csv + GeoLite2-Country-Locations-en.csv).
Accepts http(s):// URLs and file:// for local files (z. B. wenn der Container kein Internet hat).
"""
import csv
import io
import ipaddress
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# RFC 1918 private + loopback: immer in Geo-Whitelist und vor WAF-Block schützen (Docker-NAT etc.)
RFC1918_AND_LOOPBACK_CIDRS = [
    "127.0.0.0/8",   # Loopback
    "10.0.0.0/8",    # RFC 1918
    "172.16.0.0/12", # RFC 1918
    "192.168.0.0/16",# RFC 1918
]


def download_url(
    url: str,
    timeout: int = 60,
    retries: int = 1,
    retry_delay_sec: float = 0.0,
) -> bytes:
    """
    Download URL or read local file (file://). Returns raw bytes.
    For http(s): on failure retries up to retries times, waiting retry_delay_sec between attempts.
    Raises on final failure (caller can catch and e.g. send mail).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        path = urllib.parse.unquote(parsed.path)
        with open(path, "rb") as f:
            return f.read()
    last_err: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < max(1, retries) - 1 and retry_delay_sec > 0:
                logger.warning("Download attempt %s failed: %s; retry in %.0fs", attempt + 1, e, retry_delay_sec)
                time.sleep(retry_delay_sec)
    if last_err is not None:
        raise last_err
    raise RuntimeError("download_url: no attempt ran")


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


def _format_geo_line(
    network: str,
    geoname_id: int,
    locations: Dict[int, str],
    default_country: str,
) -> str:
    """Format one block line for geo map (shared by chunked and non-chunked path)."""
    country = locations.get(geoname_id, default_country)
    if not country or len(country) != 2:
        country = default_country
    return f"{network}\t{country}"


def build_geo_map(
    blocks: List[Tuple[str, int]],
    locations: Dict[int, str],
    default_country: str = "XX",
    chunk_size: int = 0,
    sleep_after_chunk_ms: int = 0,
) -> str:
    """
    Build HAProxy map_ip content: one line per network "network\tcountry_code".
    Sorted by network for deterministic output. Locations not found get default_country.
    If chunk_size > 0, process blocks in chunks and sleep sleep_after_chunk_ms after each
    chunk to yield CPU and avoid spiking the host.
    """
    lines: List[str] = []
    if chunk_size <= 0:
        for network, geoname_id in blocks:
            lines.append(_format_geo_line(network, geoname_id, locations, default_country))
    else:
        for i in range(0, len(blocks), chunk_size):
            chunk = blocks[i : i + chunk_size]
            for network, geoname_id in chunk:
                lines.append(_format_geo_line(network, geoname_id, locations, default_country))
            if sleep_after_chunk_ms > 0:
                time.sleep(sleep_after_chunk_ms / 1000.0)
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
    """Build whitelist map content: one line per IP/CIDR 'ip_or_cidr\t1' for map_ip -m found.
    Always includes RFC 1918 + loopback so private/Docker-NAT IPs are never geo-blocked;
    plus 127.0.0.1 and ANCHOR_IPS."""
    seen: set[str] = set()
    lines: List[str] = []
    for cidr in RFC1918_AND_LOOPBACK_CIDRS:
        if cidr not in seen:
            seen.add(cidr)
            lines.append(f"{cidr}\t1")
    for ip in ["127.0.0.1", *anchor_ips]:
        ip = ip.strip()
        if ip and not ip.startswith("#") and ip not in seen:
            seen.add(ip)
            lines.append(f"{ip}\t1")
    return "\n".join(lines) + "\n" if lines else ""


def fetch_geo_csv_to_map(
    blocks_url: str,
    locations_url: str,
    blocks_ipv6_url: Optional[str] = None,
    timeout: int = 60,
    retries: int = 1,
    retry_delay_sec: float = 0.0,
    chunk_size: int = 0,
    sleep_after_chunk_ms: int = 0,
) -> str:
    """
    Fetch both CSVs from URLs and return HAProxy geo map content.
    blocks_url: GeoLite2-Country-Blocks-IPv4.csv
    locations_url: GeoLite2-Country-Locations-en.csv
    blocks_ipv6_url: optional GeoLite2-Country-Blocks-IPv6.csv; merged into one map (IPv4+IPv6).
    """
    blocks_content = download_url(
        blocks_url, timeout=timeout, retries=retries, retry_delay_sec=retry_delay_sec
    )
    locations_content = download_url(
        locations_url, timeout=timeout, retries=retries, retry_delay_sec=retry_delay_sec
    )
    blocks = parse_country_blocks_csv(blocks_content)
    locations = parse_country_locations_csv(locations_content)
    if blocks_ipv6_url:
        try:
            ipv6_content = download_url(
                blocks_ipv6_url,
                timeout=timeout,
                retries=retries,
                retry_delay_sec=retry_delay_sec,
            )
            blocks.extend(parse_country_blocks_csv(ipv6_content))
        except Exception as e:
            logger.warning("IPv6 blocks download failed (continuing with IPv4 only): %s", e)
    return build_geo_map(
        blocks,
        locations,
        chunk_size=chunk_size,
        sleep_after_chunk_ms=sleep_after_chunk_ms,
    )


def _is_ip(s: str) -> bool:
    """Return True if s is a valid IPv4 or IPv6 address."""
    s = (s or "").strip()
    if not s:
        return False
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _range_to_cidrs(start: str, end: str) -> List[str]:
    """Convert IP range (start, end) to minimal list of CIDR strings. Handles IPv4 and IPv6."""
    start, end = start.strip(), end.strip()
    try:
        a = ipaddress.ip_address(start)
        b = ipaddress.ip_address(end)
    except ValueError:
        return []
    if a.version != b.version:
        return []
    if a > b:
        a, b = b, a
    try:
        return [str(net) for net in ipaddress.summarize_address_range(a, b)]
    except (ValueError, TypeError):
        return []


def _detect_csv_format(content: bytes) -> Tuple[str, bool]:
    """
    Detect CSV format: "range" (ip_range_start, ip_range_end, country_code) or "simple" (network, country).
    Returns (format_name, has_header).
    """
    text = content.decode("utf-8", errors="replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ("simple", True)
    first = [p.strip() for p in lines[0].split(",")]
    # Header check
    if first and first[0].lower() in ("ip_range_start", "network", "network_cidr"):
        has_header = True
        if first[0].lower() == "network" or (len(first) >= 1 and "network" in first[0].lower()):
            return ("simple", True)
        return ("range", True)
    # No header: first line is data
    if len(first) >= 3 and _is_ip(first[0]) and _is_ip(first[1]):
        cc = first[2].strip().upper()
        if len(cc) == 2 and cc.isalpha():
            return ("range", False)
    if len(first) >= 2 and (_is_ip(first[0]) or "/" in (first[0] or "")):
        return ("simple", False)
    return ("simple", True)


def fetch_geo_from_single_url(
    url: str,
    timeout: int = 60,
    retries: int = 1,
    retry_delay_sec: float = 0.0,
    chunk_size: int = 0,
    sleep_after_chunk_ms: int = 0,
) -> str:
    """
    If GEO_SOURCE_URL points to a single CSV: supports (1) network,country columns or
    (2) ip_range_start,ip_range_end,country_code (e.g. sapics/ip-location-db). Otherwise use
    fetch_geo_csv_to_map with GEO_BLOCKS_URL and GEO_LOCATIONS_URL.
    """
    content = download_url(
        url, timeout=timeout, retries=retries, retry_delay_sec=retry_delay_sec
    )
    fmt, has_header = _detect_csv_format(content)
    if fmt == "range":
        return _convert_range_csv_to_map(
            content,
            has_header=has_header,
            chunk_size=chunk_size,
            sleep_after_chunk_ms=sleep_after_chunk_ms,
        )
    return _convert_simple_csv_to_map(
        content,
        chunk_size=chunk_size,
        sleep_after_chunk_ms=sleep_after_chunk_ms,
    )


def _convert_range_csv_to_map(
    content: bytes,
    has_header: bool = True,
    chunk_size: int = 0,
    sleep_after_chunk_ms: int = 0,
) -> str:
    """
    Convert CSV with ip_range_start, ip_range_end, country_code (e.g. sapics/ip-location-db)
    to geo map. Ranges are converted to CIDRs. If chunk_size > 0, process in chunks and sleep.
    """
    rows: List[Tuple[str, str, str]] = []
    reader = csv.reader(io.StringIO(content.decode("utf-8", errors="replace")))
    if has_header:
        header = next(reader, None)
        if header is None:
            return ""
        # Normalize column indices (safe: use default when column missing)
        h = [c.strip().lower() for c in header]
        idx_start = next((i for i, c in enumerate(h) if c == "ip_range_start"), 0)
        idx_end = next((i for i, c in enumerate(h) if c == "ip_range_end"), 1)
        idx_cc = next((i for i, c in enumerate(h) if c == "country_code"), 2)
    else:
        idx_start, idx_end, idx_cc = 0, 1, 2
    for row in reader:
        if len(row) <= max(idx_start, idx_end, idx_cc):
            continue
        start_ip = row[idx_start].strip()
        end_ip = row[idx_end].strip()
        country = (row[idx_cc].strip() or "XX").upper()
        if len(country) != 2:
            country = "XX"
        if _is_ip(start_ip) and _is_ip(end_ip):
            rows.append((start_ip, end_ip, country))
    lines: List[str] = []
    for start_ip, end_ip, country in rows:
        for cidr in _range_to_cidrs(start_ip, end_ip):
            lines.append(f"{cidr}\t{country}")
    if chunk_size > 0 and sleep_after_chunk_ms > 0 and lines:
        # Process in chunks for CPU yielding (we already expanded, so chunk by output lines)
        pass  # sorting happens once at the end
    lines.sort(key=_sort_key_network)
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _convert_simple_csv_to_map(
    content: bytes,
    chunk_size: int = 0,
    sleep_after_chunk_ms: int = 0,
) -> str:
    """
    Convert a simple CSV with columns like network,country_code (or similar) to map.
    If chunk_size > 0, build lines in chunks and sleep to yield CPU.
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
    if chunk_size <= 0:
        lines = [f"{n}\t{c}" for n, c in rows]
    else:
        lines = []
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            lines.extend(f"{n}\t{c}" for n, c in chunk)
            if sleep_after_chunk_ms > 0:
                time.sleep(sleep_after_chunk_ms / 1000.0)
    lines.sort(key=_sort_key_network)
    return "\n".join(lines) + "\n" if lines else ""


def merge_geo_map_contents(content1: str, content2: str) -> str:
    """
    Merge two geo map contents (lines "network\\tcountry\\n") into one, sorted by network.
    Used to combine IPv4 + IPv6 when using GEO_SOURCE_URL + GEO_SOURCE_IPV6_URL.
    """
    lines: List[str] = []
    for content in (content1, content2):
        for line in content.strip().splitlines():
            line = line.strip()
            if line and "\t" in line:
                lines.append(line)
    if not lines:
        return ""
    lines.sort(key=_sort_key_network)
    return "\n".join(lines) + "\n"


def write_maps(
    map_dir: str,
    geo_content: str,
    whitelist_content: str,
    geo_path: str = "geo.map",
    whitelist_path: str = "whitelist.map",
    chunk_size: int = 0,
    sleep_after_chunk_ms: int = 0,
) -> None:
    """Write geo and whitelist map files to map_dir.
    If chunk_size > 0, write geo file in chunks with sleep to avoid I/O spikes."""
    os.makedirs(map_dir, exist_ok=True)
    geo_file = os.path.join(map_dir, geo_path)
    whitelist_file = os.path.join(map_dir, whitelist_path)
    if chunk_size <= 0:
        with open(geo_file, "w") as f:
            f.write(geo_content)
    else:
        geo_lines = geo_content.splitlines()
        with open(geo_file, "w") as f:
            for i in range(0, len(geo_lines), chunk_size):
                chunk = geo_lines[i : i + chunk_size]
                f.write("\n".join(chunk))
                if i + chunk_size < len(geo_lines):
                    f.write("\n")
                if sleep_after_chunk_ms > 0:
                    time.sleep(sleep_after_chunk_ms / 1000.0)
            if geo_content.endswith("\n"):
                f.write("\n")
    with open(whitelist_file, "w") as f:
        f.write(whitelist_content)
    logger.info("Wrote %s and %s", geo_file, whitelist_file)
