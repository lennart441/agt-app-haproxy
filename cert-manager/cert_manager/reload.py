"""
Push updated TLS certificate to HAProxy via Runtime API (set ssl cert + commit).

Uses the worker stats socket (level admin), same mechanism as the HAProxy
integration tests in tests/haproxy/test_cert_reload.py.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

CERT_SOCKET_TIMEOUT_SEC = 60


def _wait_for_socket(socket_path: str, wait_for_socket_sec: int) -> bool:
    if wait_for_socket_sec <= 0:
        return os.path.exists(socket_path)
    deadline = time.monotonic() + wait_for_socket_sec
    while not os.path.exists(socket_path):
        if time.monotonic() >= deadline:
            return False
        logger.info("Waiting for HAProxy stats socket at %s ...", socket_path)
        time.sleep(2)
    return True


def _socat_command(socket_path: str, payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["socat", "STDIO", f"UNIX-CONNECT:{socket_path}"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=CERT_SOCKET_TIMEOUT_SEC,
    )


def apply_ssl_cert(
    socket_path: str,
    crt_path: str,
    pem_bytes: bytes,
    wait_for_socket_sec: int = 30,
) -> bool:
    """
    Hot-swap the certificate in a running HAProxy via the stats socket.

    If socket_path is empty, reload is skipped (PEM on disk only).
    If the socket is not available within wait_for_socket_sec, assume HAProxy
    is not running yet and return True (PEM will load on next HAProxy start).
    """
    if not socket_path:
        logger.debug("HAPROXY_STATS_SOCKET not set; skipping runtime cert update")
        return True

    if not _wait_for_socket(socket_path, wait_for_socket_sec):
        logger.info(
            "HAProxy stats socket not available at %s; PEM written, "
            "HAProxy will load certificate on next start",
            socket_path,
        )
        return True

    pem_text = pem_bytes.decode("utf-8")
    set_payload = f"set ssl cert {crt_path} <<\n{pem_text}\n"
    commit_payload = f"commit ssl cert {crt_path}\n"

    try:
        set_result = _socat_command(socket_path, set_payload)
        commit_result = _socat_command(socket_path, commit_payload)
    except FileNotFoundError:
        logger.error("socat not found; cannot update HAProxy certificate")
        return False
    except subprocess.TimeoutExpired:
        logger.error("HAProxy cert update timed out after %ds", CERT_SOCKET_TIMEOUT_SEC)
        return False

    combined = "\n".join(
        part.strip()
        for part in (
            set_result.stdout,
            set_result.stderr,
            commit_result.stdout,
            commit_result.stderr,
        )
        if part
    )
    if set_result.returncode != 0 or commit_result.returncode != 0:
        logger.error(
            "HAProxy cert update failed (set=%s commit=%s): %s",
            set_result.returncode,
            commit_result.returncode,
            combined or "(no output)",
        )
        return False

    logger.info("HAProxy certificate updated at %s via runtime API", crt_path)
    return True
