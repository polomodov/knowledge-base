from __future__ import annotations

import ipaddress
import socket
import urllib.request
from http.client import HTTPResponse
from typing import Any
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL is rejected before any network request is made."""


def _reject_non_public_host(host: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise UnsafeUrlError(f"cannot resolve host: {host}") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise UnsafeUrlError(f"host {host} resolves to a non-public address: {address}")


def check_public_url(url: str) -> None:
    """Reject anything that is not a plain http(s) request to a public host.

    Blocks file://, ftp://, data:, and other schemes that the default urllib opener
    would happily follow (turning a fetch into a local-file/internal-service read), and
    blocks hosts that resolve to private, loopback, link-local, or otherwise non-public
    addresses (SSRF to metadata endpoints, internal services, etc.).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"unsupported URL scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    _reject_non_public_host(host, port)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        # Re-validate the redirect target so a public URL cannot bounce to file:// or an
        # internal host.
        check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public_url(url: str, *, headers: dict[str, str], timeout: float) -> HTTPResponse:
    """Open an http(s) URL for reading after validating it (and every redirect)."""
    check_public_url(url)
    request = urllib.request.Request(url, method="GET")
    for name, value in headers.items():
        request.add_header(name, value)
    opener = urllib.request.build_opener(_SafeRedirectHandler, urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)
