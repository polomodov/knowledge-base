import pytest

from knowledge_base.net import UnsafeUrlError, _resolve_public, check_public_url
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
