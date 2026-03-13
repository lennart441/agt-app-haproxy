"""Tests for zero-downtime SSL certificate reload via HAProxy Runtime API.

Uses 'set ssl cert' + 'commit ssl cert' on the stats socket – the same
mechanism the cert-manager uses in production.
"""

import threading
import time

import requests

from conftest import (
    HAPROXY_HOST,
    HAPROXY_HTTPS_PORT,
    get_server_cert_der,
    generate_self_signed_pem,
    update_ssl_cert,
)

CERT_PATH = "/etc/ssl/certs/haproxy.pem"


def test_cert_content_updated():
    """After Runtime API cert update, HAProxy serves the new certificate."""
    cert_before = get_server_cert_der(HAPROXY_HOST, HAPROXY_HTTPS_PORT)

    new_pem = generate_self_signed_pem(cn="haproxy-test-updated")
    update_ssl_cert(CERT_PATH, new_pem)
    time.sleep(0.5)

    cert_after = get_server_cert_der(HAPROXY_HOST, HAPROXY_HTTPS_PORT)
    assert cert_before != cert_after, "Certificate DER bytes should differ after update"


def test_cert_reload_no_downtime():
    """HTTPS requests succeed without errors while the certificate is swapped."""
    errors: list = []
    running = True

    def send_loop():
        while running:
            try:
                r = requests.get(
                    f"https://{HAPROXY_HOST}:{HAPROXY_HTTPS_PORT}/",
                    headers={"Host": "agt-app.de"},
                    verify=False, timeout=5,
                )
                if r.status_code >= 500:
                    errors.append(r.status_code)
            except Exception as exc:
                errors.append(str(exc))
            time.sleep(0.02)

    thread = threading.Thread(target=send_loop, daemon=True)
    thread.start()

    try:
        for i in range(3):
            pem = generate_self_signed_pem(cn=f"reload-test-{i}")
            update_ssl_cert(CERT_PATH, pem)
            time.sleep(0.3)
    finally:
        running = False
        thread.join(timeout=5)

    assert not errors, f"Errors during cert reload: {errors}"
