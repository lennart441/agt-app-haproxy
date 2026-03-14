"""
Leader logic: read local certbot files, build haproxy.pem, validate and update state.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .config import Config
from .metrics import inc_deploy_failure, inc_deploy_success
from .state import set_state_from_pem

logger = logging.getLogger(__name__)


def _read_file(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            data = f.read()
        if not data:
            logger.warning("File %s is empty", path)
            return None
        return data
    except FileNotFoundError:
        logger.warning("File %s not found", path)
        return None
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def build_combined_pem(config: Config) -> Optional[bytes]:
    """
    Build a combined PEM (fullchain + privkey) for HAProxy.

    Returns bytes or None if source files are missing/invalid.
    """
    if not config.source_fullchain or not config.source_privkey:
        logger.info("CERT_SOURCE_FULLCHAIN or CERT_SOURCE_PRIVKEY not set; skipping.")
        return None

    fullchain = _read_file(config.source_fullchain)
    privkey = _read_file(config.source_privkey)
    if fullchain is None or privkey is None:
        return None

    if b"BEGIN CERTIFICATE" not in fullchain:
        logger.warning("Fullchain file does not look like a certificate")
        return None
    if b"BEGIN PRIVATE KEY" not in privkey and b"BEGIN RSA PRIVATE KEY" not in privkey:
        logger.warning("Privkey file does not look like a private key")
        return None

    combined = fullchain.rstrip() + b"\n" + privkey.lstrip()
    return combined


def write_target_pem(config: Config, pem_bytes: bytes) -> None:
    """
    Atomically write PEM to target path (via temporary file + rename).
    """
    target = config.target_pem_path
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = target + ".new"
    with open(tmp_path, "wb") as f:
        f.write(pem_bytes)
    os.replace(tmp_path, target)
    logger.info("Wrote new certificate to %s", target)


def run_leader_once(config: Config) -> bool:
    """
    Single leader iteration: build PEM, write it, update state.

    Returns True on success, False otherwise.
    """
    pem = build_combined_pem(config)
    if pem is None:
        inc_deploy_failure()
        return False
    write_target_pem(config, pem)
    set_state_from_pem(pem)
    inc_deploy_success()
    return True

