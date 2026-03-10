import json

from cert_manager.config import Config
from cert_manager.follower import get_master_status


class DummyResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self.payload = payload

    def to_http(self) -> tuple[int, bytes]:
        return self.status, json.dumps(self.payload).encode("utf-8")


def test_get_master_status_disables_on_multiple_masters(monkeypatch):
    cfg = Config.from_env()
    cfg.mesh_nodes = ["10.0.0.1", "10.0.0.2"]

    master_payload = {
        "node_prio": 1,
        "node_name": "agt-1",
        "cert_is_master": True,
        "version": "abc",
        "validated_since": "2024-01-01T00:00:00+00:00",
    }
    responses = {
        ("10.0.0.1", cfg.status_port, "/cert/status"): DummyResponse(
            200, master_payload
        ).to_http(),
        ("10.0.0.2", cfg.status_port, "/cert/status"): DummyResponse(
            200, master_payload
        ).to_http(),
    }

    def fake_http_get(host: str, port: int, path: str, timeout: float = 5.0):
        return responses[(host, port, path)]

    monkeypatch.setattr("cert_manager.follower._http_get", fake_http_get)

    result = get_master_status(cfg)
    assert result is None

