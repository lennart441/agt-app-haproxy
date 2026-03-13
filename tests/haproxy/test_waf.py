"""Tests for Coraza WAF (SPOE integration + auto-ban logic).

The Coraza config includes a test rule:
  SecRule ARGS:testwaf "123" → deny 403 (id:190001)

The auto-ban (gpc0 in st_waf_blocks) only applies to non-internal IPs.
Since the Docker test network is in 172.x.x.x (internal_networks ACL),
auto-ban tracking does NOT fire.  We test this explicitly.
"""

import requests

from conftest import haproxy_cmd

API_HOST = "agt-1.agt-app.de"


def test_waf_test_rule_blocks(base_url):
    """?testwaf=123 triggers Coraza test rule → 403 via be_403_waf."""
    r = requests.get(
        f"{base_url}/v3/sync-api/test?testwaf=123",
        headers={"Host": API_HOST},
        verify=False, timeout=10,
    )
    assert r.status_code == 403


def test_waf_normal_request_passes(base_url):
    """A clean request is not blocked by Coraza."""
    r = requests.get(
        f"{base_url}/v3/sync-api/test",
        headers={"Host": API_HOST},
        verify=False, timeout=10,
    )
    assert r.status_code == 200


def test_waf_sql_injection_blocked(base_url):
    """Classic SQL injection payload triggers CRS rules → 403."""
    r = requests.get(
        f"{base_url}/v3/sync-api/test",
        headers={"Host": API_HOST},
        params={"id": "1' UNION SELECT username,password FROM users--"},
        verify=False, timeout=10,
    )
    assert r.status_code == 403


def test_waf_xss_blocked(base_url):
    """XSS payload triggers CRS rules → 403."""
    r = requests.get(
        f"{base_url}/v3/sync-api/test",
        headers={"Host": API_HOST},
        params={"q": "<script>alert('XSS')</script>"},
        verify=False, timeout=10,
    )
    assert r.status_code == 403


def test_waf_rce_blocked(base_url):
    """Remote code execution payload triggers CRS rules → 403."""
    r = requests.get(
        f"{base_url}/v3/sync-api/test",
        headers={"Host": API_HOST},
        params={"cmd": "; cat /etc/passwd"},
        verify=False, timeout=10,
    )
    assert r.status_code == 403


def test_internal_ip_not_auto_banned(base_url):
    """Docker IPs (172.x) are in internal_networks → no WAF auto-ban.

    After a WAF-deny, the next clean request must still succeed because
    the gpc0 counter in st_waf_blocks is not incremented for internal IPs.
    """
    # Trigger WAF deny
    r1 = requests.get(
        f"{base_url}/v3/sync-api/test?testwaf=123",
        headers={"Host": API_HOST},
        verify=False, timeout=10,
    )
    assert r1.status_code == 403

    # Verify st_waf_blocks has NO entry for our IP
    table = haproxy_cmd("show table st_waf_blocks")
    assert "gpc0=1" not in table, (
        "Internal IP should not be auto-banned: " + table
    )

    # A clean request must succeed
    r2 = requests.get(
        f"{base_url}/v3/sync-api/test",
        headers={"Host": API_HOST},
        verify=False, timeout=10,
    )
    assert r2.status_code == 200


def test_waf_ban_check_via_stick_table(base_url):
    """Manually injecting gpc0 into st_waf_blocks bans external IPs.

    We inject a ban for a dummy IP and verify the table entry exists.
    (We cannot test the actual deny from an internal IP because the ban
    check skips internal_networks, but we verify the table mechanics.)
    """
    # Inject a ban entry for a public IP
    haproxy_cmd("set table st_waf_blocks key 203.0.113.42 data.gpc0 1")
    table = haproxy_cmd("show table st_waf_blocks")
    assert "203.0.113.42" in table or "gpc0=1" in table
