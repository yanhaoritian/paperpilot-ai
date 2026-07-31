from __future__ import annotations

from ipaddress import ip_address, ip_network

from fastapi import Request


def _normalized_ip(value: str) -> str | None:
    candidate = (value or "").strip().strip('"')
    if not candidate:
        return None
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    try:
        return str(ip_address(candidate))
    except ValueError:
        pass
    if candidate.count(":") == 1 and "." in candidate:
        host, _port = candidate.rsplit(":", 1)
        try:
            return str(ip_address(host))
        except ValueError:
            return None
    return None


def client_ip(request: Request, trusted_proxy_cidrs: str) -> str:
    """Resolve the client without trusting forwarding headers from public peers."""
    peer = _normalized_ip(request.client.host if request.client else "")
    if peer is None:
        return "unknown"

    networks = []
    for raw in (trusted_proxy_cidrs or "").split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            continue

    def trusted(value: str) -> bool:
        parsed = ip_address(value)
        return any(
            parsed.version == network.version and parsed in network
            for network in networks
        )

    if not trusted(peer):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    chain = [
        normalized
        for item in forwarded.split(",")
        if (normalized := _normalized_ip(item)) is not None
    ]
    if not chain:
        real_ip = _normalized_ip(request.headers.get("x-real-ip", ""))
        return real_ip or peer

    chain.append(peer)
    while len(chain) > 1 and trusted(chain[-1]):
        chain.pop()
    return chain[-1]
