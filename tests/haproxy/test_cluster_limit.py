"""Tests for the cluster-wide connection limit (CLUSTER_MAXCONN).

The test environment sets CLUSTER_MAXCONN=5.  Both the HTTP and HTTPS
frontends track connections in st_global_conn and deny with 503 when the
limit is reached.

Strategy: send requests to the /slow endpoint (which takes ~3 s to respond)
so that conn_cur stays elevated long enough for new requests to see it.
With CLUSTER_MAXCONN=5, the 5th concurrent connection triggers 'ge 5'.
"""

import concurrent.futures
import time

import requests

from conftest import HAPROXY_HOST, HAPROXY_HTTPS_PORT, HAPROXY_HTTP_PORT

MAXCONN = 5  # must match TEST_CLUSTER_MAXCONN in docker-compose.test.yaml
API_HOST = "agt-1.agt-app.de"


def _send_slow(base_url):
    """Send a request to the slow endpoint (keeps connection ~3 s)."""
    try:
        return requests.get(
            f"{base_url}/v3/sync-api/slow",
            headers={"Host": API_HOST},
            verify=False, timeout=15,
        ).status_code
    except Exception:
        return -1


def test_cluster_maxconn_503(base_url):
    """When conn_cur >= CLUSTER_MAXCONN, new HTTPS requests get 503.

    Open MAXCONN slow connections (the last one already hits the limit),
    then verify a fast request also gets 503.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAXCONN + 2) as pool:
        # Start slow requests that keep connections alive
        slow_futures = [pool.submit(_send_slow, base_url) for _ in range(MAXCONN)]
        time.sleep(1.5)

        # The next fast request should see conn_cur >= MAXCONN
        r = requests.get(
            f"{base_url}/",
            headers={"Host": "agt-app.de"},
            verify=False, timeout=5,
        )
        fast_code = r.status_code

        # Collect slow results (some may be 503 themselves)
        slow_codes = [f.result() for f in slow_futures]

    all_codes = slow_codes + [fast_code]
    assert 503 in all_codes, (
        f"Expected at least one 503 with CLUSTER_MAXCONN={MAXCONN}, "
        f"got: { {c: all_codes.count(c) for c in set(all_codes)} }"
    )


def test_cluster_maxconn_on_http(base_url):
    """conn_cur limit also applies on port 80 (shared st_global_conn)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAXCONN + 2) as pool:
        slow_futures = [pool.submit(_send_slow, base_url) for _ in range(MAXCONN)]
        time.sleep(1.5)

        r = requests.get(
            f"http://{HAPROXY_HOST}:{HAPROXY_HTTP_PORT}/",
            headers={"Host": "agt-app.de"},
            allow_redirects=False,
            timeout=5,
        )
        fast_code = r.status_code

        slow_codes = [f.result() for f in slow_futures]

    all_codes = slow_codes + [fast_code]
    assert 503 in all_codes, (
        f"Expected at least one 503 on HTTP port, "
        f"got: { {c: all_codes.count(c) for c in set(all_codes)} }"
    )


def test_connections_recover_after_slow(base_url):
    """After slow connections close, new requests succeed again."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAXCONN + 2) as pool:
        slow_futures = [pool.submit(_send_slow, base_url) for _ in range(MAXCONN)]
        # Wait for all slow responses to complete
        concurrent.futures.wait(slow_futures, timeout=15)

    time.sleep(1)

    r = requests.get(
        f"{base_url}/",
        headers={"Host": "agt-app.de"},
        verify=False, timeout=5,
    )
    assert r.status_code == 200
