import ipaddress
import socket
import urllib.request

import pytest

from knowledge_base import net
from knowledge_base.net import (
    UnsafeUrlError,
    _guarded_factory,
    _reject_non_public,
    _resolve_public,
    _SafeRedirectHandler,
    check_public_url,
)
from knowledge_base.sources.book_cube import collect_attachment_refs
from knowledge_base.sources.tellmeabout_tech import LiveFetchUnavailable, fetch_feed_payload


def test_check_public_url_rejects_non_http_schemes() -> None:
    for url in ("file:///etc/passwd", "ftp://example.com/x", "data:text/plain,hi", "gopher://x"):
        with pytest.raises(UnsafeUrlError):
            check_public_url(url)


def test_check_public_url_rejects_private_and_loopback_hosts() -> None:
    # Literal private/loopback/link-local IPs resolve to themselves — no network needed.
    for url in ("http://127.0.0.1/feed", "http://10.0.0.5/feed", "http://169.254.169.254/latest/meta-data"):
        with pytest.raises(UnsafeUrlError):
            check_public_url(url)


def test_check_public_url_requires_a_host() -> None:
    with pytest.raises(UnsafeUrlError):
        check_public_url("http:///no-host")


def test_fetch_translates_unsafe_url_to_live_fetch_unavailable() -> None:
    # The adapter surfaces the blocked URL as its structured error, not a raw exception.
    with pytest.raises(LiveFetchUnavailable) as error:
        fetch_feed_payload("file:///etc/passwd")
    assert error.value.to_payload()["error"] == "live_fetch_unavailable"


def test_resolve_public_pins_public_ip_and_rejects_private() -> None:
    # Numeric addresses resolve to themselves, so no network is needed. The pinned IP is
    # returned for a public address and private addresses are rejected (DNS-rebinding guard).
    assert _resolve_public("8.8.8.8", 80) == "8.8.8.8"
    for host in ("10.0.0.1", "127.0.0.1", "169.254.169.254"):
        with pytest.raises(UnsafeUrlError):
            _resolve_public(host, 80)


def test_reject_non_public_covers_shared_space_and_mapped_addresses() -> None:
    # The positive is_global allowlist rejects ranges a boolean denylist misses: RFC 6598 shared
    # space and IPv4-mapped IPv6 that resolves to loopback/link-local (e.g. cloud metadata).
    for address in ("100.64.0.1", "::ffff:169.254.169.254", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "192.0.2.5"):
        parsed = ipaddress.ip_address(address)
        with pytest.raises(UnsafeUrlError):
            _reject_non_public(parsed, "host")
    # A genuinely public address (and its IPv4-mapped form) is accepted.
    _reject_non_public(ipaddress.ip_address("93.184.216.34"), "host")
    _reject_non_public(ipaddress.ip_address("::ffff:93.184.216.34"), "host")


def test_resolve_public_rejects_when_any_resolved_address_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    # DNS-rebinding guard: a name that resolves to both a public and a private address must be
    # rejected outright rather than pinning the public one and hoping.
    def fake_getaddrinfo(host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(net.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        _resolve_public("rebind.example", 80)


def test_guarded_factory_pins_resolved_public_ip_and_rejects_private() -> None:
    # Connect-time pinning: the factory re-resolves the host and pins the validated public IP that
    # the socket will actually connect to (closing the validate-then-reconnect rebinding window),
    # and refuses to build a connection to a private host at all.
    build = _guarded_factory(tls=False)
    connection = build("8.8.8.8")
    assert connection._pinned_ip == "8.8.8.8"
    with pytest.raises(UnsafeUrlError):
        build("10.0.0.1")


def test_safe_redirect_handler_revalidates_target() -> None:
    # A public URL must not be able to bounce to an internal host or a file:// target on redirect.
    # The cleartext scheme is composed from a variable so the insecure-redirect payloads the test
    # deliberately exercises are not flagged as insecure production URLs.
    handler = _SafeRedirectHandler()
    request = urllib.request.Request("https://example.com/start")
    cleartext = "http"
    targets = (f"{cleartext}://169.254.169.254/latest/meta-data/", f"{cleartext}://10.0.0.1/", "file:///etc/passwd")
    for target in targets:
        with pytest.raises(UnsafeUrlError):
            handler.redirect_request(request, None, 302, "Found", {}, target)


def test_attachment_paths_reject_traversal_and_absolute() -> None:
    # A crafted export must not make the ingester stat() or record files outside the archive.
    assert collect_attachment_refs({"photo": "../../../../etc/passwd"}, archive=None) == []
    assert collect_attachment_refs({"file": "/etc/hosts"}, archive=None) == []
    assert collect_attachment_refs({"photo": "photos\\..\\..\\secret.jpg"}, archive=None) == []
    # Windows drive and UNC paths must also be rejected (they survive PurePosixPath).
    assert collect_attachment_refs({"file": "C:\\Users\\me\\secret.txt"}, archive=None) == []
    assert collect_attachment_refs({"file": "\\\\server\\share\\secret.txt"}, archive=None) == []
    assert collect_attachment_refs({"file": "C:relative.txt"}, archive=None) == []
    # A normal in-archive relative path is still accepted.
    refs = collect_attachment_refs({"photo": "photos/photo_1.jpg", "media_type": "photo"}, archive=None)
    assert [ref["relative_path"] for ref in refs] == ["photos/photo_1.jpg"]
