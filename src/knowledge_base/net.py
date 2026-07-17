from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.request
from http.client import HTTPResponse
from typing import Any
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL is rejected before or during connection setup."""


def _reject_non_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str) -> None:
    # Unwrap IPv4-mapped IPv6 (e.g. ``::ffff:169.254.169.254``) so the classification runs on the
    # embedded IPv4 address; on Python < 3.12.4 the IPv6 properties do not see through the mapping,
    # which would otherwise let a cloud-metadata address slip past a link-local/private check.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    # Positive allowlist rather than a boolean denylist: reject anything that is not a globally
    # routable public address. `is_global` is the inverse of the full IANA special-purpose registry,
    # so it also covers ranges a denylist misses — RFC 6598 shared space (100.64.0.0/10), the
    # documentation blocks, and future special-purpose allocations — and fails closed by default.
    if not address.is_global:
        raise UnsafeUrlError(f"host {host} resolves to a non-public address: {address}")


def _resolve_public(host: str, port: int) -> str:
    """Resolve `host`, require every result to be public, and return one pinned IP.

    Returning the address that is actually connected to (rather than re-resolving the
    hostname at connect time) closes the DNS-rebinding gap where a low-TTL name resolves
    to a public IP during validation and a private/metadata IP during the request.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise UnsafeUrlError(f"cannot resolve host: {host}") from error
    if not infos:
        raise UnsafeUrlError(f"cannot resolve host: {host}")
    for info in infos:
        _reject_non_public(ipaddress.ip_address(info[4][0]), host)
    return str(infos[0][4][0])


def check_public_url(url: str) -> None:
    """Reject anything that is not a plain http(s) request to a public host.

    Blocks file://, ftp://, data:, and other schemes the default urllib opener would
    follow (turning a fetch into a local-file/internal-service read) and hosts that
    resolve to private, loopback, link-local, or otherwise non-public addresses.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"unsupported URL scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    _resolve_public(host, port)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:  # type: ignore[attr-defined]
            self._tunnel()  # type: ignore[attr-defined]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:  # type: ignore[attr-defined]
            self.sock = sock
            self._tunnel()  # type: ignore[attr-defined]
            sock = self.sock
        # TLS validates SNI / certificate against the real hostname, not the pinned IP.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


def _guarded_factory(tls: bool) -> Any:
    def build(host: str, **kwargs: Any) -> http.client.HTTPConnection:
        connection_class = _PinnedHTTPSConnection if tls else _PinnedHTTPConnection
        # Let http.client parse host[:port] (incl. IPv6), then validate + pin that host.
        probe = connection_class(host, pinned_ip="", **kwargs)
        probe._pinned_ip = _resolve_public(probe.host, probe.port)
        return probe

    return build


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: Any) -> Any:
        return self.do_open(_guarded_factory(tls=False), req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: Any) -> Any:
        return self.do_open(_guarded_factory(tls=True), req)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        # Re-validate the redirect target so a public URL cannot bounce to file:// or an
        # internal host; the guarded handler re-resolves and re-pins on the re-open.
        check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public_url(url: str, *, headers: dict[str, str], timeout: float) -> HTTPResponse:
    """Open an http(s) URL for reading, validating and address-pinning it and every redirect."""
    check_public_url(url)
    request = urllib.request.Request(url, method="GET")
    for name, value in headers.items():
        request.add_header(name, value)
    # Build a minimal http(s)-only opener instead of urllib.request.build_opener, which also
    # registers FileHandler/FTPHandler/DataHandler. check_public_url already rejects non-http(s)
    # schemes on the initial request and on every redirect, but omitting those handlers entirely
    # means a bug that ever bypassed that check still cannot turn a fetch into a local-file,
    # ftp, or data read. An unknown scheme now raises URLError (no UnknownHandler) rather than
    # being served.
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),
        _GuardedHTTPHandler(),
        _GuardedHTTPSHandler(),
        _SafeRedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener.open(request, timeout=timeout)
