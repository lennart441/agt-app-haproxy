"""
Trigger HAProxy to reload maps (runtime API or socket).
"""
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)


def trigger_reload(socket_path: str, wait_for_socket_sec: int = 30) -> bool:
    """
    Send 'reload' to HAProxy via stats socket. Uses socat if available.
    If socket is missing, wait up to wait_for_socket_sec (poll every 2s) for HAProxy to create it.
    Returns True on success.
    """
    deadline = time.monotonic() + wait_for_socket_sec
    while not os.path.exists(socket_path):
        if time.monotonic() >= deadline:
            logger.error("Socket not found: %s (waited %ds)", socket_path, wait_for_socket_sec)
            return False
        logger.info("Waiting for HAProxy socket at %s ...", socket_path)
        time.sleep(2)
    try:
        result = subprocess.run(
            ["sh", "-c", f'echo "reload" | socat stdio "{socket_path}"'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.error("socat reload failed: %s", result.stderr)
            return False
        return True
    except FileNotFoundError:
        logger.error("socat not found")
        return False
    except subprocess.TimeoutExpired:
        logger.error("reload timed out")
        return False
