import struct

from dashboard.docker_client import DockerClient, _strip_docker_log_headers


def test_strip_docker_log_headers_stdout():
    """Docker log stream: stdout frames with 8-byte header."""
    line1 = b"2026-03-14T10:00:00Z Hello world\n"
    line2 = b"2026-03-14T10:00:01Z Second line\n"
    # stream_type=1 (stdout), padding=0,0,0, then 4-byte big-endian size
    header1 = b"\x01\x00\x00\x00" + struct.pack(">I", len(line1))
    header2 = b"\x01\x00\x00\x00" + struct.pack(">I", len(line2))
    raw = header1 + line1 + header2 + line2
    result = _strip_docker_log_headers(raw)
    assert "Hello world" in result
    assert "Second line" in result


def test_strip_docker_log_headers_stderr():
    """Docker log stream: stderr frames (stream_type=2)."""
    line = b"ERROR: something broke\n"
    header = b"\x02\x00\x00\x00" + struct.pack(">I", len(line))
    raw = header + line
    result = _strip_docker_log_headers(raw)
    assert "ERROR: something broke" in result


def test_strip_docker_log_headers_empty():
    result = _strip_docker_log_headers(b"")
    assert result == ""


def test_strip_docker_log_headers_plain_text_fallback():
    """If raw data doesn't have Docker multiplexed headers, return as-is."""
    raw = b"plain text without headers"
    result = _strip_docker_log_headers(raw)
    assert "plain text without headers" in result


def test_docker_client_list_containers_no_socket():
    """DockerClient with non-existent socket returns empty list."""
    client = DockerClient("/tmp/nonexistent-docker.sock")
    result = client.list_containers()
    assert result == []


def test_docker_client_get_logs_no_socket():
    """DockerClient with non-existent socket returns error string."""
    client = DockerClient("/tmp/nonexistent-docker.sock")
    result = client.get_container_logs("test-container")
    assert result.startswith("Error:")
