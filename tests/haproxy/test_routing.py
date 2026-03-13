"""Tests for HAProxy routing, host classification, and error pages."""

import requests


def test_http_redirect_to_https(http_url, base_url):
    """Port 80 redirects to HTTPS for allowed geo/whitelisted IPs."""
    r = requests.get(
        f"{http_url}/some-page",
        headers={"Host": "agt-app.de"},
        allow_redirects=False,
        timeout=5,
    )
    assert r.status_code == 301
    assert r.headers["Location"].startswith("https://")


def test_unknown_host_returns_404(base_url):
    """A request with an unrecognized Host header lands on backend_404_error."""
    r = requests.get(
        f"{base_url}/",
        headers={"Host": "unknown.example.com"},
        verify=False, timeout=5,
    )
    assert r.status_code == 404


def test_api_sync_route(base_url):
    """agt-1.agt-app.de/v3/sync-api routes to api_backend_sync (port 3111)."""
    r = requests.get(
        f"{base_url}/v3/sync-api/test",
        headers={"Host": "agt-1.agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200
    assert "api_sync" in r.text


def test_api_report_route(base_url):
    """agt-1.agt-app.de/v3/report routes to api_backend_report (port 3112)."""
    r = requests.get(
        f"{base_url}/v3/report/test",
        headers={"Host": "agt-1.agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200
    assert "api_report" in r.text


def test_api_primaer_route(base_url):
    """agt-1.agt-app.de/v3/pri-api routes to api_backend_primaer (port 3113)."""
    r = requests.get(
        f"{base_url}/v3/pri-api/test",
        headers={"Host": "agt-1.agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200
    assert "api_primaer" in r.text


def test_api_get_route(base_url):
    """agt-1.agt-app.de/v3/agt-get-api routes to api_backend_get (port 3114)."""
    r = requests.get(
        f"{base_url}/v3/agt-get-api/test",
        headers={"Host": "agt-1.agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200
    assert "api_get" in r.text


def test_website_route(base_url):
    """agt-app.de/ routes to website_backend (port 3102)."""
    r = requests.get(
        f"{base_url}/",
        headers={"Host": "agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200
    assert "website" in r.text


def test_dashboard_route(base_url):
    """agt-app.de/dashboard routes to dashboard_backend_apache (port 3102).

    Longer map_beg prefix wins over the website catch-all.
    """
    r = requests.get(
        f"{base_url}/dashboard",
        headers={"Host": "agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200


def test_client_route(base_url):
    """client.agt-app.de/ routes to client_backend_apache (port 3101)."""
    r = requests.get(
        f"{base_url}/",
        headers={"Host": "client.agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200
    assert "client" in r.text


def test_api_host_without_path_404(base_url):
    """API host with no known API path returns 404 (no catch-all backend)."""
    r = requests.get(
        f"{base_url}/nonexistent",
        headers={"Host": "agt-1.agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 404


def test_all_three_api_hosts_work(base_url):
    """All three API host names (agt-1/2/3) route to backends."""
    for host in ("agt-1.agt-app.de", "agt-2.agt-app.de", "agt-3.agt-app.de"):
        r = requests.get(
            f"{base_url}/v3/sync-api/test",
            headers={"Host": host},
            verify=False, timeout=5,
        )
        assert r.status_code == 200, f"Host {host} returned {r.status_code}"
