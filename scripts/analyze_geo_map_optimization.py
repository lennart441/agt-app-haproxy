#!/usr/bin/env python3
"""
Build and analyze geo.map optimization potential via CIDR aggregation.

Usage examples:
  python scripts/analyze_geo_map_optimization.py \
    --ipv4-url "https://...ipv4.csv" \
    --ipv6-url "https://...ipv6.csv"

  python scripts/analyze_geo_map_optimization.py \
    --env-file "1.env"
"""

from __future__ import annotations

import argparse
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from geo_manager.fetcher import fetch_geo_from_single_url, merge_geo_map_contents


@dataclass(frozen=True)
class MapStats:
    line_count: int
    byte_count: int


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _parse_geo_lines(content: str) -> List[Tuple[ipaddress._BaseNetwork, str]]:
    entries: List[Tuple[ipaddress._BaseNetwork, str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        network_str, country = line.split("\t", 1)
        network_str = network_str.strip()
        country = country.strip().upper()
        if len(country) != 2:
            continue
        try:
            network = ipaddress.ip_network(network_str, strict=False)
        except ValueError:
            continue
        entries.append((network, country))
    return entries


def _aggregate_entries(
    entries: Iterable[Tuple[ipaddress._BaseNetwork, str]]
) -> List[Tuple[ipaddress._BaseNetwork, str]]:
    grouped: Dict[Tuple[int, str], List[ipaddress._BaseNetwork]] = {}
    for network, country in entries:
        grouped.setdefault((network.version, country), []).append(network)

    collapsed: List[Tuple[ipaddress._BaseNetwork, str]] = []
    for (_, country), networks in grouped.items():
        # collapse_addresses only merges where it is lossless/aligned.
        for net in ipaddress.collapse_addresses(networks):
            collapsed.append((net, country))
    return sorted(
        collapsed,
        key=lambda item: (
            item[0].version,
            int(item[0].network_address),
            item[0].prefixlen,
            item[1],
        ),
    )


def _entries_to_map_content(entries: Iterable[Tuple[ipaddress._BaseNetwork, str]]) -> str:
    lines = [f"{net}\t{country}" for net, country in entries]
    return ("\n".join(lines) + "\n") if lines else ""


def _stats_for(content: str) -> MapStats:
    line_count = sum(
        1
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "\t" in line
    )
    byte_count = len(content.encode("utf-8"))
    return MapStats(line_count=line_count, byte_count=byte_count)


def _format_pct(before: int, after: int) -> str:
    if before <= 0:
        return "0.00%"
    reduction = ((before - after) / before) * 100.0
    return f"{reduction:.2f}%"


def _resolve_urls(args: argparse.Namespace) -> Tuple[str, str]:
    ipv4_url = (args.ipv4_url or "").strip()
    ipv6_url = (args.ipv6_url or "").strip()
    if args.env_file:
        env_values = _parse_env_file(Path(args.env_file))
        if not ipv4_url:
            ipv4_url = (env_values.get("GEO_SOURCE_URL") or "").strip()
        if not ipv6_url:
            ipv6_url = (env_values.get("GEO_SOURCE_IPV6_URL") or "").strip()
    if not ipv4_url:
        raise ValueError("No IPv4 source URL found. Use --ipv4-url or provide it in --env-file.")
    return ipv4_url, ipv6_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze geo.map optimization potential.")
    parser.add_argument("--ipv4-url", help="CSV URL for IPv4 source data.")
    parser.add_argument("--ipv6-url", default="", help="Optional CSV URL for IPv6 source data.")
    parser.add_argument("--env-file", default="", help="Optional .env file to read GEO_SOURCE_URL* from.")
    parser.add_argument(
        "--output-dir",
        default="tmp/geo-analysis",
        help="Directory for generated original/optimized map files.",
    )
    args = parser.parse_args()

    ipv4_url, ipv6_url = _resolve_urls(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading/building geo map from IPv4 URL: {ipv4_url}")
    original_content = fetch_geo_from_single_url(ipv4_url)
    if ipv6_url:
        print(f"Downloading/building geo map from IPv6 URL: {ipv6_url}")
        ipv6_content = fetch_geo_from_single_url(ipv6_url)
        if ipv6_content.strip():
            original_content = merge_geo_map_contents(original_content, ipv6_content)

    original_path = output_dir / "geo.original.map"
    original_path.write_text(original_content, encoding="utf-8")

    parsed_entries = _parse_geo_lines(original_content)
    optimized_entries = _aggregate_entries(parsed_entries)
    optimized_content = _entries_to_map_content(optimized_entries)

    optimized_path = output_dir / "geo.optimized.map"
    optimized_path.write_text(optimized_content, encoding="utf-8")

    before = _stats_for(original_content)
    after = _stats_for(optimized_content)

    print("")
    print("=== Geo map optimization analysis ===")
    print(f"Original map   : {original_path}")
    print(f"Optimized map  : {optimized_path}")
    print(f"Lines before   : {before.line_count}")
    print(f"Lines after    : {after.line_count}")
    print(f"Line reduction : {_format_pct(before.line_count, after.line_count)}")
    print(f"Bytes before   : {before.byte_count}")
    print(f"Bytes after    : {after.byte_count}")
    print(f"Byte reduction : {_format_pct(before.byte_count, after.byte_count)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
