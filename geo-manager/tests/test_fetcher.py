"""Tests for geo_manager.fetcher."""
import io
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from geo_manager.fetcher import (
    build_geo_map,
    build_whitelist_map,
    download_url,
    parse_country_blocks_csv,
    parse_country_locations_csv,
    write_maps,
    _convert_simple_csv_to_map,
    _convert_range_csv_to_map,
    _detect_csv_format,
    _is_ip,
    _range_to_cidrs,
    _sort_key_network,
    fetch_geo_csv_to_map,
    fetch_geo_from_single_url,
    merge_geo_map_contents,
)


def test_parse_country_blocks_csv():
    content = b"network,geoname_id,registered_country_geoname_id\n1.0.0.0/24,123,123\n2.0.0.0/24,456,456"
    rows = parse_country_blocks_csv(content)
    assert len(rows) == 2
    assert rows[0] == ("1.0.0.0/24", 123)
    assert rows[1] == ("2.0.0.0/24", 456)


def test_parse_country_blocks_csv_invalid_geoname_id_uses_zero():
    content = b"network,geoname_id\n1.0.0.0/24,not_a_number"
    rows = parse_country_blocks_csv(content)
    assert len(rows) == 1
    assert rows[0] == ("1.0.0.0/24", 0)


def test_parse_country_blocks_csv_registered_country_geoname_id():
    content = b"network,geoname_id,registered_country_geoname_id\n1.0.0.0/24,,456"
    rows = parse_country_blocks_csv(content)
    assert len(rows) == 1
    assert rows[0] == ("1.0.0.0/24", 456)


def test_parse_country_blocks_csv_empty_network_skipped():
    content = b"network,geoname_id\n,123\n2.0.0.0/24,456"
    rows = parse_country_blocks_csv(content)
    assert len(rows) == 1
    assert rows[0][0] == "2.0.0.0/24"


def test_parse_country_locations_csv():
    content = b"geoname_id,country_iso_code,locale_code\n123,DE,\n456,US,"
    result = parse_country_locations_csv(content)
    assert result[123] == "DE"
    assert result[456] == "US"


def test_parse_country_locations_csv_invalid_geoname_skipped():
    content = b"geoname_id,country_iso_code\nx,DE\n1,AT"
    result = parse_country_locations_csv(content)
    assert 1 in result
    assert result[1] == "AT"


def test_build_geo_map():
    blocks = [("10.0.0.0/8", 1), ("192.168.0.0/24", 2)]
    locations = {1: "DE", 2: "AT"}
    out = build_geo_map(blocks, locations)
    assert "10.0.0.0/8\tDE" in out
    assert "192.168.0.0/24\tAT" in out


def test_build_geo_map_missing_location_defaults():
    blocks = [("10.0.0.0/8", 999)]
    locations = {}
    out = build_geo_map(blocks, locations, default_country="XX")
    assert "10.0.0.0/8\tXX" in out


def test_build_geo_map_empty():
    out = build_geo_map([], {})
    assert out == ""


def test_build_geo_map_invalid_country_len_becomes_default():
    blocks = [("10.0.0.0/8", 1)]
    locations = {1: "DEU"}  # 3 chars -> default
    out = build_geo_map(blocks, locations, default_country="XX")
    assert "10.0.0.0/8\tXX" in out


def test_build_geo_map_chunked_same_result():
    """Chunked build produces same output as non-chunked."""
    blocks = [("10.0.0.0/8", 1), ("192.168.0.0/24", 2)]
    locations = {1: "DE", 2: "AT"}
    with patch("geo_manager.fetcher.time.sleep"):
        out = build_geo_map(
            blocks, locations, chunk_size=1, sleep_after_chunk_ms=1
        )
    assert "10.0.0.0/8\tDE" in out
    assert "192.168.0.0/24\tAT" in out


def test_build_geo_map_chunked_with_default_country():
    """Chunked build with missing/invalid location uses default_country."""
    blocks = [("10.0.0.0/8", 999)]  # 999 not in locations
    locations = {}
    out = build_geo_map(
        blocks, locations, default_country="XX",
        chunk_size=1, sleep_after_chunk_ms=0
    )
    assert "10.0.0.0/8\tXX" in out


def test_build_geo_map_chunked_with_sleep():
    """Chunked build with sleep_after_chunk_ms > 0 calls time.sleep."""
    blocks = [("10.0.0.0/8", 1)]
    locations = {1: "DE"}
    with patch("geo_manager.fetcher.time.sleep") as mock_sleep:
        out = build_geo_map(
            blocks, locations, chunk_size=1, sleep_after_chunk_ms=50
        )
    assert "10.0.0.0/8\tDE" in out
    mock_sleep.assert_called()


def test_sort_key_network():
    assert _sort_key_network("10.0.0.0/8\tDE") != (0, 0)
    assert _sort_key_network("invalid\tXX") == (0, 0)


def test_merge_geo_map_contents():
    c1 = "10.0.0.0/8\tDE\n192.168.0.0/24\tAT\n"
    c2 = "2001:db8::/32\tDE\n"
    out = merge_geo_map_contents(c1, c2)
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert "10.0.0.0/8\tDE" in out
    assert "192.168.0.0/24\tAT" in out
    assert "2001:db8::/32\tDE" in out
    # Sorted by network
    assert lines[0].startswith("10.")
    assert lines[1].startswith("192.")
    assert lines[2].startswith("2001:")


def test_merge_geo_map_contents_empty():
    assert merge_geo_map_contents("", "") == ""
    assert merge_geo_map_contents("1.0.0.0/24\tDE\n", "").strip() == "1.0.0.0/24\tDE"


def test_is_ip():
    assert _is_ip("1.0.0.1") is True
    assert _is_ip("2001:db8::1") is True
    assert _is_ip("") is False
    assert _is_ip("1.0.0.0/24") is False
    assert _is_ip("x") is False


def test_range_to_cidrs():
    assert _range_to_cidrs("1.0.0.0", "1.0.0.255") == ["1.0.0.0/24"]
    assert _range_to_cidrs("10.0.0.0", "10.0.0.3") == ["10.0.0.0/30"]
    assert _range_to_cidrs("1.0.0.0", "1.0.0.0") == ["1.0.0.0/32"]
    # Start > end: order is swapped
    assert _range_to_cidrs("1.0.0.255", "1.0.0.0") == ["1.0.0.0/24"]
    # Invalid
    assert _range_to_cidrs("x", "y") == []
    # Mixed IPv4/IPv6 -> []
    assert _range_to_cidrs("1.0.0.0", "::1") == []
    # summarize_address_range can raise (e.g. invalid range); except returns []
    with patch("geo_manager.fetcher.ipaddress.summarize_address_range", side_effect=ValueError("bad")):
        assert _range_to_cidrs("10.0.0.0", "10.0.0.255") == []
    # IPv6
    cidrs = _range_to_cidrs("2001:db8::", "2001:db8::ff")
    assert len(cidrs) >= 1
    assert any("2001:db8" in c for c in cidrs)


def test_detect_csv_format():
    # Range, no header (sapics style)
    out = _detect_csv_format(b"1.0.1.0,1.0.3.255,CN\n")
    assert out == ("range", False)
    # Range, with header
    out = _detect_csv_format(b"ip_range_start,ip_range_end,country_code\n1.0.0.0,1.0.0.255,DE\n")
    assert out == ("range", True)
    # Simple, with header (network)
    out = _detect_csv_format(b"network,country_iso_code\n1.0.0.0/24,DE\n")
    assert out == ("simple", True)
    # Simple, with header network_cidr
    out = _detect_csv_format(b"network_cidr,country\n10.0.0.0/8,AT\n")
    assert out == ("simple", True)
    # Empty content
    out = _detect_csv_format(b"")
    assert out == ("simple", True)
    # Two columns with CIDR -> simple, no header
    out = _detect_csv_format(b"1.0.0.0/24,DE\n")
    assert out == ("simple", False)
    # Unrecognized first line -> fallback simple with header
    out = _detect_csv_format(b"foo,bar,baz\n")
    assert out == ("simple", True)


def test_convert_range_csv_to_map_headerless():
    """sapics/ip-location-db: no header, ip_start,ip_end,country_code."""
    content = b"1.0.1.0,1.0.3.255,CN\n10.0.0.0,10.0.0.255,DE\n"
    out = _convert_range_csv_to_map(content, has_header=False)
    assert "1.0.1.0/24\tCN" in out
    assert "1.0.2.0/23\tCN" in out
    assert "10.0.0.0/24\tDE" in out


def test_convert_range_csv_to_map_with_header():
    content = b"ip_range_start,ip_range_end,country_code\n1.0.0.0,1.0.0.255,DE\n192.168.1.0,192.168.1.255,AT\n"
    out = _convert_range_csv_to_map(content, has_header=True)
    assert "1.0.0.0/24\tDE" in out
    assert "192.168.1.0/24\tAT" in out


def test_convert_range_csv_to_map_with_chunk_params():
    """With chunk_size and sleep_after_chunk_ms set, the chunk branch is taken (pass)."""
    content = b"1.0.0.0,1.0.0.255,DE\n"
    with patch("geo_manager.fetcher.time.sleep"):
        out = _convert_range_csv_to_map(
            content, has_header=False, chunk_size=1, sleep_after_chunk_ms=1
        )
    assert "1.0.0.0/24\tDE" in out


def test_convert_range_csv_to_map_empty_header_only():
    """Header only, no data rows -> empty map."""
    out = _convert_range_csv_to_map(b"ip_range_start,ip_range_end,country_code\n", has_header=True)
    assert out == ""


def test_convert_range_csv_to_map_empty_content_with_header():
    """Empty content with has_header=True -> return ''."""
    out = _convert_range_csv_to_map(b"", has_header=True)
    assert out == ""


def test_convert_range_csv_to_map_header_missing_country_code_column():
    """Header without country_code falls back to positional 0,1,2."""
    content = b"ip_range_start,ip_range_end\n1.0.0.0,1.0.0.255,DE\n"
    out = _convert_range_csv_to_map(content, has_header=True)
    assert "1.0.0.0/24\tDE" in out


def test_convert_range_csv_to_map_skips_invalid_country_and_invalid_ip():
    """Rows with non-2-char country become XX; invalid IPs skipped."""
    content = b"1.0.0.0,1.0.0.255,XXX\n10.0.0.0,10.0.0.0,DE\ninvalid,10.0.0.1,DE\n"
    out = _convert_range_csv_to_map(content, has_header=False)
    assert "1.0.0.0/24\tXX" in out
    assert "10.0.0.0/32\tDE" in out


def test_convert_range_csv_to_map_single_char_country_becomes_xx():
    """Country code with len != 2 is normalized to XX."""
    content = b"10.0.0.0,10.0.0.0,D\n"
    out = _convert_range_csv_to_map(content, has_header=False)
    assert "10.0.0.0/32\tXX" in out


def test_convert_range_csv_to_map_all_invalid_rows_empty_output():
    """Only invalid IP rows -> empty map (lines empty)."""
    content = b"invalid,invalid,DE\n"
    out = _convert_range_csv_to_map(content, has_header=False)
    assert out == ""


def test_convert_range_csv_to_map_all_ranges_empty_cidrs_empty_output():
    """Valid rows but _range_to_cidrs returns [] for all (e.g. mixed IPv4/IPv6) -> return ''."""
    content = b"1.0.0.0,::1,DE\n"
    out = _convert_range_csv_to_map(content, has_header=False)
    assert out == ""


def test_convert_range_csv_to_map_skips_short_rows():
    """Rows with too few columns are skipped (continue)."""
    content = b"1.0.0.0,1.0.0.255\n10.0.0.0,10.0.0.0,DE\n"
    out = _convert_range_csv_to_map(content, has_header=False)
    assert "10.0.0.0/32\tDE" in out


@patch("geo_manager.fetcher.download_url")
def test_fetch_geo_from_single_url_range_format(mock_dl):
    """fetch_geo_from_single_url mit sapics Range-Format (headerlos)."""
    mock_dl.return_value = b"1.0.0.0,1.0.0.255,DE\n8.8.8.0,8.8.8.255,US\n"
    out = fetch_geo_from_single_url("http://example.com/geo.csv")
    assert "1.0.0.0/24\tDE" in out
    assert "8.8.8.0/24\tUS" in out


def test_build_whitelist_map():
    out = build_whitelist_map(["8.8.8.8", "1.1.1.1"])
    assert "8.8.8.8\t1" in out
    assert "1.1.1.1\t1" in out


def test_build_whitelist_map_empty():
    out = build_whitelist_map([])
    assert out == ""


def test_build_whitelist_map_skips_comments():
    out = build_whitelist_map(["# comment", "8.8.8.8"])
    assert "8.8.8.8" in out
    assert "#" not in out or "comment" not in out


def test_convert_simple_csv_to_map():
    content = b"network,country_iso_code\n1.0.0.0/24,DE\n2.0.0.0/24,US"
    out = _convert_simple_csv_to_map(content)
    assert "1.0.0.0/24\tDE" in out
    assert "2.0.0.0/24\tUS" in out


def test_convert_simple_csv_to_map_alternative_columns():
    content = b"network_cidr,country\n10.0.0.0/8,AT"
    out = _convert_simple_csv_to_map(content)
    assert "10.0.0.0/8\tAT" in out


def test_convert_simple_csv_to_map_invalid_country_becomes_xx():
    content = b"network,country\n1.0.0.0/24,XXX"
    out = _convert_simple_csv_to_map(content)
    assert "XX" in out


def test_write_maps(tmp_path):
    write_maps(str(tmp_path), "1.0.0.0/24\tDE\n", "8.8.8.8\t1\n")
    assert (tmp_path / "geo.map").read_text() == "1.0.0.0/24\tDE\n"
    assert (tmp_path / "whitelist.map").read_text() == "8.8.8.8\t1\n"


def test_write_maps_chunked_same_result(tmp_path):
    """Chunked write produces same files as non-chunked."""
    geo = "1.0.0.0/24\tDE\n2.0.0.0/24\tAT\n"
    with patch("geo_manager.fetcher.time.sleep"):
        write_maps(
            str(tmp_path), geo, "8.8.8.8\t1\n",
            chunk_size=1, sleep_after_chunk_ms=1
        )
    assert (tmp_path / "geo.map").read_text() == geo
    assert (tmp_path / "whitelist.map").read_text() == "8.8.8.8\t1\n"


def test_download_url():
    with patch("urllib.request.urlopen") as m:
        m.return_value.__enter__.return_value.read.return_value = b"data"
        data = download_url("http://example.com/x", timeout=5)
    assert data == b"data"


def test_download_url_file_scheme(tmp_path):
    """file:// wird gelesen, kein Netzwerk nötig."""
    f = tmp_path / "geo.csv"
    f.write_bytes(b"network,country_iso_code\n1.0.0.0/24,DE\n")
    data = download_url(f"file://{f}")
    assert data == b"network,country_iso_code\n1.0.0.0/24,DE\n"


@patch("geo_manager.fetcher.download_url")
def test_fetch_geo_csv_to_map(mock_dl):
    mock_dl.side_effect = [
        b"network,geoname_id\n1.0.0.0/24,1\n2.0.0.0/24,2",
        b"geoname_id,country_iso_code\n1,DE\n2,US",
    ]
    out = fetch_geo_csv_to_map("http://a/blocks.csv", "http://a/loc.csv")
    assert "1.0.0.0/24\tDE" in out
    assert "2.0.0.0/24\tUS" in out


@patch("geo_manager.fetcher.download_url")
def test_fetch_geo_from_single_url(mock_dl):
    mock_dl.return_value = b"network,country_iso_code\n1.0.0.0/24,DE\n"
    out = fetch_geo_from_single_url("http://example.com/geo.csv")
    assert "1.0.0.0/24\tDE" in out


def test_convert_simple_csv_to_map_chunked_same_result():
    content = b"network,country_iso_code\n1.0.0.0/24,DE\n2.0.0.0/24,US\n"
    with patch("geo_manager.fetcher.time.sleep"):
        out = _convert_simple_csv_to_map(
            content, chunk_size=1, sleep_after_chunk_ms=1
        )
    assert "1.0.0.0/24\tDE" in out
    assert "2.0.0.0/24\tUS" in out


@patch("geo_manager.fetcher.download_url")
def test_fetch_geo_csv_to_map_chunk_params(mock_dl):
    mock_dl.side_effect = [
        b"network,geoname_id\n1.0.0.0/24,1\n2.0.0.0/24,2",
        b"geoname_id,country_iso_code\n1,DE\n2,US",
    ]
    with patch("geo_manager.fetcher.time.sleep"):
        out = fetch_geo_csv_to_map(
            "http://a/blocks.csv", "http://a/loc.csv",
            chunk_size=1, sleep_after_chunk_ms=1
        )
    assert "1.0.0.0/24\tDE" in out
    assert "2.0.0.0/24\tUS" in out


def test_download_url_retries_then_succeeds():
    resp = MagicMock()
    resp.read.return_value = b"data"
    ctx = MagicMock()
    ctx.__enter__.return_value = resp
    ctx.__exit__.return_value = None
    with patch("urllib.request.urlopen") as m:
        m.side_effect = [OSError("net"), OSError("net"), ctx]
        data = download_url("http://example.com/x", timeout=5, retries=3, retry_delay_sec=0.01)
    assert data == b"data"


def test_download_url_retries_all_fail():
    with patch("urllib.request.urlopen") as m:
        m.side_effect = OSError("net")
        with pytest.raises(OSError):
            download_url("http://example.com/x", timeout=5, retries=2, retry_delay_sec=0.01)


def test_download_url_no_attempt_ran_raises():
    """Cover defensive raise when loop runs zero times."""
    with patch("geo_manager.fetcher.range", lambda *a: iter([])):
        with pytest.raises(RuntimeError, match="no attempt ran"):
            download_url("http://example.com/x", retries=1)


@patch("geo_manager.fetcher.download_url")
def test_fetch_geo_csv_to_map_with_ipv6_url(mock_dl):
    mock_dl.side_effect = [
        b"network,geoname_id\n1.0.0.0/24,1",
        b"geoname_id,country_iso_code\n1,DE",
        b"network,geoname_id\n2001:db8::/32,1",
    ]
    out = fetch_geo_csv_to_map(
        "http://a/blocks.csv", "http://a/loc.csv",
        blocks_ipv6_url="http://a/blocks-ipv6.csv"
    )
    assert "1.0.0.0/24\tDE" in out
    assert "2001:db8::/32\tDE" in out
    assert mock_dl.call_count == 3


@patch("geo_manager.fetcher.download_url")
def test_fetch_geo_csv_to_map_ipv6_download_fails_continues(mock_dl):
    mock_dl.side_effect = [
        b"network,geoname_id\n1.0.0.0/24,1",
        b"geoname_id,country_iso_code\n1,DE",
        OSError("ipv6 unreachable"),
    ]
    out = fetch_geo_csv_to_map(
        "http://a/blocks.csv", "http://a/loc.csv",
        blocks_ipv6_url="http://a/ipv6.csv"
    )
    assert "1.0.0.0/24\tDE" in out
