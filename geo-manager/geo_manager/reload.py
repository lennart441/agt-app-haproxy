"""
Trigger HAProxy to reload maps (Master-CLI socket, master-worker mode).
"""
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

# Reload kann dauern (Config parsen, neuer Worker starten). HAProxy-Doku: ggf. -t300 bei socat.
RELOAD_SOCKET_TIMEOUT_SEC = 120


def trigger_reload(socket_path: str, wait_for_socket_sec: int = 30) -> bool:
    """
    Send 'reload' to HAProxy Master-CLI socket (erfordert -W -S …). Lädt Config und Maps neu.
    Wenn der Socket fehlt, bis wait_for_socket_sec warten (Poll alle 2s).
    Liefert True nur bei Success=1 in der Antwort.
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
            ["socat", "STDIO", f"UNIX-CONNECT:{socket_path}"],
            input="reload\n",
            capture_output=True,
            text=True,
            timeout=RELOAD_SOCKET_TIMEOUT_SEC,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = f"{out}\n{err}".strip()
        if result.returncode != 0:
            logger.error("socat reload failed (code %s): %s", result.returncode, err or out)
            return False
        if "Success=1" in combined:
            logger.debug("HAProxy reload Success=1")
            return True
        if "Success=0" in combined:
            logger.error("HAProxy reload Success=0 (new config/worker failed). %s", combined)
            return False
        # Master-CLI kann Success=1 teils nicht in unser stdout liefern (Leer/Stderr);
        # bei returncode 0 und leerem Output war der Reload in der Praxis erfolgreich.
        if not combined:
            logger.info(
                "HAProxy reload: no Success=1/0 in response (stdout/stderr empty); exit 0, assuming success."
            )
            return True
        logger.warning(
                "HAProxy reload response unclear (no Success=1/0). stdout: %r stderr: %r", out, err
            )
        return False
    except FileNotFoundError:
        logger.error("socat not found")
        return False
    except subprocess.TimeoutExpired:
        logger.error("reload timed out after %ds", RELOAD_SOCKET_TIMEOUT_SEC)
        return False
