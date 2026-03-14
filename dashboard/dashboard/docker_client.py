"""
Docker Engine REST API client over Unix socket (stdlib only).

Provides container listing and log retrieval without external dependencies.
"""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection subclass that connects via a Unix domain socket."""

    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class DockerClient:
    """Minimal Docker Engine API client for container inspection and logs."""

    def __init__(self, socket_path: str = "/var/run/docker.sock") -> None:
        self.socket_path = socket_path

    def _request(
        self, method: str, path: str, timeout: float = 5.0
    ) -> tuple[int, bytes]:
        conn = UnixHTTPConnection(self.socket_path, timeout=timeout)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, body
        finally:
            conn.close()

    def list_containers(self, all_containers: bool = True) -> list[dict[str, Any]]:
        """List Docker containers. Returns parsed JSON list."""
        path = "/containers/json?all=true" if all_containers else "/containers/json"
        try:
            status, body = self._request("GET", path)
            if status != 200:
                return []
            return json.loads(body.decode("utf-8"))
        except Exception:
            return []

    def get_container_logs(
        self,
        container_name: str,
        tail: int = 200,
        timestamps: bool = True,
    ) -> str:
        """Retrieve stdout+stderr logs for a container by name."""
        ts = "true" if timestamps else "false"
        path = (
            f"/containers/{container_name}/logs"
            f"?stdout=1&stderr=1&tail={tail}&timestamps={ts}"
        )
        try:
            status, body = self._request("GET", path, timeout=10.0)
            if status != 200:
                return f"Error: HTTP {status}"
            return _strip_docker_log_headers(body)
        except Exception as exc:
            return f"Error: {exc}"


def _strip_docker_log_headers(raw: bytes) -> str:
    """Strip Docker multiplexed stream headers (8-byte prefix per frame).

    Docker log API prepends each frame with an 8-byte header:
    [stream_type(1) | padding(3) | size(4, big-endian)].
    Stream type is 0 (stdin), 1 (stdout), or 2 (stderr).
    """
    if len(raw) < 8 or raw[0] not in (0, 1, 2):
        return raw.decode("utf-8", errors="replace")

    lines: list[str] = []
    pos = 0
    while pos + 8 <= len(raw):
        stream_type = raw[pos]
        if stream_type not in (0, 1, 2):
            lines.append(raw[pos:].decode("utf-8", errors="replace"))
            break
        size = int.from_bytes(raw[pos + 4 : pos + 8], "big")
        start = pos + 8
        end = start + size
        if end > len(raw):
            end = len(raw)
        chunk = raw[start:end]
        text = chunk.decode("utf-8", errors="replace").rstrip("\n")
        if text:
            lines.append(text)
        pos = end
    if not lines and raw:
        return raw.decode("utf-8", errors="replace")
    return "\n".join(lines)
