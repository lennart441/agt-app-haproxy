"""
Trigger HAProxy to reload maps (runtime API or socket).
"""
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def trigger_reload(socket_path: str) -> bool:
    """
    Send 'reload' to HAProxy via stats socket. Uses socat if available.
    Returns True on success.
    """
    if not os.path.exists(socket_path):
        logger.error("Socket not found: %s", socket_path)
        return False
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
