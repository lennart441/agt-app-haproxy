"""Shared fixtures for HAProxy integration tests.

Provides helpers for the HAProxy Runtime API (stats socket), stick-table
management, map manipulation, and SSL cert operations.
"""

import os
import socket as _socket
import ssl
import subprocess
import time

import pytest
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
HAPROXY_HOST = os.environ.get("HAPROXY_HOST", "haproxy")
HAPROXY_HTTPS_PORT = int(os.environ.get("HAPROXY_HTTPS_PORT", "443"))
HAPROXY_HTTP_PORT = int(os.environ.get("HAPROXY_HTTP_PORT", "80"))
STATS_SOCKET = os.environ.get("HAPROXY_STATS_SOCKET", "/var/run/haproxy-stat/socket")

# ---------------------------------------------------------------------------
# HAProxy map paths (inside the container)
# ---------------------------------------------------------------------------
GEO_MAP = "/usr/local/etc/haproxy/maps/geo.map"
WHITELIST_MAP = "/usr/local/etc/haproxy/maps/whitelist.map"
RATE_LIMITS_MAP = "/usr/local/etc/haproxy/maps/rate-limits.map"
OVERLOAD_LIMITS_MAP = "/usr/local/etc/haproxy/maps/overload-limits.map"

# ---------------------------------------------------------------------------
# Default map contents (for restoring after tests)
# ---------------------------------------------------------------------------
GEO_MAP_DEFAULTS = [
    ("0.0.0.0/0", "DE"),
    ("::/0", "DE"),
]
WHITELIST_MAP_DEFAULTS = [
    ("127.0.0.0/8", "1"),
    ("10.0.0.0/8", "1"),
    ("172.16.0.0/12", "1"),
    ("192.168.0.0/16", "1"),
    ("::1/128", "1"),
    ("fe80::/10", "1"),
    ("fc00::/7", "1"),
]
RATE_LIMIT_DEFAULTS = {
    "api_get": "20",
    "api_sync": "1500",
    "api_report": "10",
    "api_primaer": "120",
    "api_primaer_reqcode": "30",
    "api_primaer_verify": "20",
    "website": "2000",
    "client": "2000",
}
OVERLOAD_LIMIT_DEFAULTS = {
    "api_sync": "200",
    "api_primaer": "100",
    "api_get": "50",
    "api_report": "30",
}

ALL_STICK_TABLES = [
    "st_global_conn",
    "st_rl_api_sync",
    "st_rl_api_get",
    "st_rl_api_report",
    "st_rl_api_primaer",
    "st_rl_api_primaer_reqcode",
    "st_rl_api_primaer_verify",
    "st_rl_website",
    "st_rl_client",
    "st_overload",
    "st_waf_blocks",
]


# ---------------------------------------------------------------------------
# HAProxy Runtime API helpers
# ---------------------------------------------------------------------------
def haproxy_cmd(cmd: str) -> str:
    """Send a single command to the HAProxy stats socket, return response."""
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(STATS_SOCKET)
    sock.sendall((cmd + "\n").encode())
    sock.shutdown(_socket.SHUT_WR)
    parts: list[bytes] = []
    while True:
        try:
            data = sock.recv(4096)
            if not data:
                break
            parts.append(data)
        except _socket.timeout:
            break
    sock.close()
    return b"".join(parts).decode(errors="replace")


def clear_table(name: str) -> None:
    haproxy_cmd(f"clear table {name}")


def set_map(map_path: str, key: str, value: str) -> str:
    return haproxy_cmd(f"set map {map_path} {key} {value}")


def add_map(map_path: str, key: str, value: str) -> str:
    return haproxy_cmd(f"add map {map_path} {key} {value}")


def clear_map(map_path: str) -> str:
    return haproxy_cmd(f"clear map {map_path}")


def restore_map(map_path: str, defaults: list[tuple[str, str]]) -> None:
    """Clear a map and rebuild it from *defaults*."""
    clear_map(map_path)
    for key, val in defaults:
        add_map(map_path, key, val)


def restore_rate_limits() -> None:
    for key, val in RATE_LIMIT_DEFAULTS.items():
        set_map(RATE_LIMITS_MAP, key, val)


def restore_overload_limits() -> None:
    for key, val in OVERLOAD_LIMIT_DEFAULTS.items():
        set_map(OVERLOAD_LIMITS_MAP, key, val)


# ---------------------------------------------------------------------------
# SSL helpers
# ---------------------------------------------------------------------------
def get_server_cert_der(host: str, port: int) -> bytes:
    """Connect via TLS and return the server certificate in DER encoding."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with _socket.create_connection((host, port), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


def generate_self_signed_pem(cn: str = "test-cert") -> str:
    """Generate a self-signed PEM (cert + key) and return its content."""
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", "/tmp/_test_key.pem",
            "-out", "/tmp/_test_cert.pem",
            "-days", "1", "-nodes",
            "-subj", f"/CN={cn}",
        ],
        check=True, capture_output=True,
    )
    with open("/tmp/_test_cert.pem") as f:
        cert = f.read()
    with open("/tmp/_test_key.pem") as f:
        key = f.read()
    return cert + key


def update_ssl_cert(cert_path: str, pem_content: str) -> str:
    """Push a new PEM to HAProxy via the Runtime API (set + commit)."""
    payload = f"set ssl cert {cert_path} <<\n{pem_content}\n"
    r1 = subprocess.run(
        ["socat", "stdio", f"UNIX-CONNECT:{STATS_SOCKET}"],
        input=payload.encode(), capture_output=True, timeout=10,
    )
    r2 = subprocess.run(
        ["socat", "stdio", f"UNIX-CONNECT:{STATS_SOCKET}"],
        input=f"commit ssl cert {cert_path}\n".encode(),
        capture_output=True, timeout=10,
    )
    return r1.stdout.decode() + "\n" + r2.stdout.decode()


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def base_url():
    return f"https://{HAPROXY_HOST}:{HAPROXY_HTTPS_PORT}"


@pytest.fixture(scope="session")
def http_url():
    return f"http://{HAPROXY_HOST}:{HAPROXY_HTTP_PORT}"


@pytest.fixture(scope="session", autouse=True)
def wait_for_haproxy(base_url):
    """Block until HAProxy accepts HTTPS connections and the stats socket is reachable."""
    deadline = time.monotonic() + 90
    last_err = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(
                f"{base_url}/",
                headers={"Host": "agt-app.de"},
                verify=False, timeout=3,
            )
            if r.status_code in (200, 301, 403, 404):
                haproxy_cmd("show info")
                return
        except Exception as exc:
            last_err = exc
        time.sleep(1)
    pytest.fail(f"HAProxy not ready after 90 s – last error: {last_err}")


@pytest.fixture(autouse=True)
def reset_tables():
    """Clear all stick tables before every test for deterministic state."""
    for tbl in ALL_STICK_TABLES:
        clear_table(tbl)
    time.sleep(0.15)
