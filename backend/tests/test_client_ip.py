from __future__ import annotations

from types import SimpleNamespace

from starlette.datastructures import Headers

from app.services.client_ip import client_ip


def _request(peer: str, headers: dict[str, str] | None = None):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer),
        headers=Headers(headers or {}),
    )


def test_untrusted_peer_cannot_spoof_forwarded_address():
    request = _request(
        "198.51.100.20",
        {"x-forwarded-for": "203.0.113.9"},
    )
    assert client_ip(request, "127.0.0.1/32") == "198.51.100.20"


def test_trusted_proxy_chain_returns_nearest_untrusted_client():
    request = _request(
        "172.18.0.1",
        {"x-forwarded-for": "192.0.2.99, 203.0.113.9, 172.18.0.2"},
    )
    assert (
        client_ip(request, "127.0.0.1/32,172.16.0.0/12")
        == "203.0.113.9"
    )


def test_trusted_proxy_supports_x_real_ip_fallback():
    request = _request(
        "127.0.0.1",
        {"x-real-ip": "203.0.113.10"},
    )
    assert client_ip(request, "127.0.0.1/32") == "203.0.113.10"
