from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import Request

from app.api import routes

_FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "fly-client-ip")


def direct_loopback(request: Request) -> bool:
    """Require both socket and Host to be local, excluding forwarded requests."""
    if request.client is None or any(name in request.headers for name in _FORWARDING_HEADERS):
        return False
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    origin = request.headers.get("origin")
    if origin:
        allowed = {str(request.base_url).rstrip("/")}
        allowed.update(("http://localhost:5173", "http://127.0.0.1:5173"))
        try:
            origin_host = urlsplit(origin).hostname or ""
            origin_local = origin_host == "localhost" or ip_address(origin_host).is_loopback
        except ValueError:
            origin_local = False
        if not origin_local or origin.rstrip("/") not in allowed:
            return False
    try:
        peer_local = ip_address(request.client.host).is_loopback
    except ValueError:
        return False
    host = request.url.hostname or ""
    try:
        host_local = ip_address(host).is_loopback
    except ValueError:
        host_local = host == "localhost"
    return peer_local and host_local


def may_access_private_data(request: Request) -> bool:
    owner = bool(routes.settings.admin_refresh_secret) and routes._is_admin(request)
    return owner or direct_loopback(request)
