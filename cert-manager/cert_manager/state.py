"""
In-memory state for the current certificate version and validation timestamp.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class CertState:
    version: str
    validated_since: datetime


_lock = threading.Lock()
_state: Optional[CertState] = None


def compute_version(pem_bytes: bytes) -> str:
    """Return a stable version identifier (SHA256 hex) for a PEM."""
    digest = hashlib.sha256(pem_bytes).hexdigest()
    return digest


def set_state_from_pem(pem_bytes: bytes) -> CertState:
    """Set global state from new PEM and return it."""
    version = compute_version(pem_bytes)
    now = datetime.now(timezone.utc)
    state = CertState(version=version, validated_since=now)
    with _lock:
        global _state
        _state = state
    return state


def get_state() -> Optional[CertState]:
    with _lock:
        return _state

