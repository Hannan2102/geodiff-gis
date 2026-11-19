"""Rate-limit keys cannot be chosen by the client."""

from starlette.requests import Request

from geodiff import ratelimit
from geodiff.config import Settings, get_settings


def req(headers: dict[str, str], peer: str = "10.0.0.9") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234),
        }
    )


def test_default_uses_peer_and_ignores_forwarded_for(monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "get_settings", lambda: Settings(client_ip_header=""))
    assert ratelimit.client_key(req({"X-Forwarded-For": "1.2.3.4"})) == "10.0.0.9"


def test_trusted_edge_header(monkeypatch) -> None:
    monkeypatch.setattr(
        ratelimit, "get_settings", lambda: Settings(client_ip_header="CF-Connecting-IP")
    )
    r = req({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "1.2.3.4"})
    assert ratelimit.client_key(r) == "203.0.113.7"
    assert ratelimit.client_key(req({})) == "10.0.0.9"  # header missing: fall back to peer


def test_forwarded_for_is_never_trusted(monkeypatch) -> None:
    monkeypatch.setattr(
        ratelimit, "get_settings", lambda: Settings(client_ip_header="X-Forwarded-For")
    )
    assert ratelimit.client_key(req({"X-Forwarded-For": "1.2.3.4"})) == "10.0.0.9"


def test_git_sha_from_render(monkeypatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc1234")
    assert Settings().git_sha == "abc1234"
    get_settings.cache_clear()
