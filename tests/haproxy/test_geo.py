"""Tests for Geo-IP blocking and runtime map updates.

Default geo.map: 0.0.0.0/0 → DE (fail-open, everyone allowed).
Default whitelist.map: RFC1918 / loopback → 1 (always allowed).
Allowed countries (test env): DE, AT, CH.

Since the test-runner IP is in 172.x (Docker bridge → whitelist), the
whitelist must be cleared to actually test geo-blocking.
"""

import threading
import time

import requests

from conftest import (
    GEO_MAP,
    GEO_MAP_DEFAULTS,
    WHITELIST_MAP,
    WHITELIST_MAP_DEFAULTS,
    clear_map,
    add_map,
    set_map,
    restore_map,
)


def _restore_geo():
    restore_map(GEO_MAP, GEO_MAP_DEFAULTS)
    restore_map(WHITELIST_MAP, WHITELIST_MAP_DEFAULTS)


def test_geo_default_allows_all(base_url):
    """With default geo.map (0.0.0.0/0 → DE), all requests are allowed."""
    r = requests.get(
        f"{base_url}/",
        headers={"Host": "agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200


def test_geo_blocked_country_gets_403(base_url):
    """After mapping the test IP to a blocked country, requests get 403."""
    try:
        # Remove whitelist so Docker IPs are not exempt
        clear_map(WHITELIST_MAP)
        # Map everything to a non-allowed country
        clear_map(GEO_MAP)
        add_map(GEO_MAP, "0.0.0.0/0", "XX")
        add_map(GEO_MAP, "::/0", "XX")
        time.sleep(0.2)

        r = requests.get(
            f"{base_url}/",
            headers={"Host": "agt-app.de"},
            verify=False, timeout=5,
        )
        assert r.status_code == 403
    finally:
        _restore_geo()


def test_geo_whitelist_bypasses_block(base_url):
    """An IP in whitelist.map bypasses geo-blocking even for country XX.

    The default whitelist already contains Docker's 172.16.0.0/12 range.
    We only modify geo.map (not the whitelist) so the whitelist remains
    intact and the request is still allowed despite geo being XX.
    """
    try:
        set_map(GEO_MAP, "0.0.0.0/0", "XX")
        set_map(GEO_MAP, "::/0", "XX")
        time.sleep(0.2)

        r = requests.get(
            f"{base_url}/",
            headers={"Host": "agt-app.de"},
            verify=False, timeout=5,
        )
        assert r.status_code == 200
    finally:
        _restore_geo()


def test_http_frontend_geo_block(http_url):
    """Port 80 returns 403-geo instead of redirect for blocked countries."""
    try:
        clear_map(WHITELIST_MAP)
        clear_map(GEO_MAP)
        add_map(GEO_MAP, "0.0.0.0/0", "XX")
        add_map(GEO_MAP, "::/0", "XX")
        time.sleep(0.2)

        r = requests.get(
            f"{http_url}/",
            headers={"Host": "agt-app.de"},
            allow_redirects=False,
            timeout=5,
        )
        assert r.status_code == 403
    finally:
        _restore_geo()


def test_geo_map_reload_no_downtime(base_url):
    """Updating geo.map via Runtime API causes no 5xx for concurrent requests."""
    errors: list[int] = []
    running = True

    def send_loop():
        while running:
            try:
                r = requests.get(
                    f"{base_url}/",
                    headers={"Host": "agt-app.de"},
                    verify=False, timeout=5,
                )
                if r.status_code >= 500:
                    errors.append(r.status_code)
            except Exception:
                pass
            time.sleep(0.02)

    thread = threading.Thread(target=send_loop, daemon=True)
    thread.start()

    try:
        for _ in range(10):
            set_map(GEO_MAP, "0.0.0.0/0", "AT")
            time.sleep(0.05)
            set_map(GEO_MAP, "0.0.0.0/0", "DE")
            time.sleep(0.05)
    finally:
        running = False
        thread.join(timeout=5)
        restore_map(GEO_MAP, GEO_MAP_DEFAULTS)

    assert not errors, f"5xx errors during map updates: {errors}"
