"""Tests for :mod:`openbiliclaw.sources.x_client`.

All tests run offline: the network-touching seam (``_raw_search`` /
``_raw_for_you`` / ``_raw_user_tweets``) is monkeypatched, so ``twitter_cli``
is never actually driven against x.com.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

from openbiliclaw import network
from openbiliclaw.sources.x_client import (
    XAuthError,
    XBlockedError,
    XClient,
    XMissingCookieError,
    XRateLimitError,
)


def _make_tweet(rest_id: str, text: str = "hello world"):
    """Build a real ``twitter_cli.models.Tweet`` so the production
    ``tweet_to_dict`` serializer runs against it (no network)."""
    from twitter_cli.models import Author, Metrics, Tweet

    return Tweet(
        id=rest_id,
        text=text,
        author=Author(id="42", name="Handle", screen_name="handle"),
        metrics=Metrics(likes=10, retweets=2, replies=1, views=999),
        created_at="Tue Jun 03 12:00:00 +0000 2025",
    )


def _fake_raw_search(query: str, *, count: int, product: str):
    return [_make_tweet("1790000000000000001"), _make_tweet("1790000000000000002")]


def test_xclient_disabled_path_does_not_import_twitter_cli() -> None:
    """Importing the module must not import twitter_cli at module load time."""
    # Snapshot affected modules so the reimport below doesn't mint NEW exception
    # class objects that linger in sys.modules — otherwise a later
    # ``from x_client import XMissingCookieError`` gets a class that fails the
    # isinstance checks in x_health (loaded with the original classes), silently
    # breaking unrelated health tests run in the same session.
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "openbiliclaw.sources.x_client"
        or name == "twitter_cli"
        or name.startswith("twitter_cli.")
    }
    try:
        for name in list(sys.modules):
            if name == "twitter_cli" or name.startswith("twitter_cli."):
                sys.modules.pop(name, None)
        sys.modules.pop("openbiliclaw.sources.x_client", None)
        importlib.import_module("openbiliclaw.sources.x_client")
        assert "twitter_cli" not in sys.modules, "twitter_cli must be lazily imported"
    finally:
        sys.modules.update(saved)
        # The reimport also rebound the PARENT package attribute
        # ``openbiliclaw.sources.x_client`` to the throwaway module. Restore it
        # too, else ``import ...x_client as m`` (parent-attr lookup) and
        # ``from ...x_client import X`` (sys.modules lookup) resolve to DIFFERENT
        # module objects in later tests, silently defeating monkeypatches.
        import openbiliclaw.sources as _sources_pkg

        _sources_pkg.x_client = saved["openbiliclaw.sources.x_client"]


def test_missing_cookie_raises_before_any_call() -> None:
    with pytest.raises(XMissingCookieError):
        # No ct0 present -> missing cookie, raised lazily on use.
        client = XClient(cookie="auth_token=onlytoken")
        client._auth_pair()


def test_missing_cookie_blank_string() -> None:
    with pytest.raises(XMissingCookieError):
        XClient(cookie="")._auth_pair()


def test_xclient_applies_project_proxy_to_twitter_cli_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from twitter_cli import client as twitter_client

    seen: list[str | None] = []

    class FakeTwitterClient:
        def __init__(self, auth_token: str, ct0: str) -> None:
            assert (auth_token, ct0) == ("a", "b")
            assert twitter_client._cffi_session is None
            seen.append(os.environ.get("TWITTER_PROXY"))

    monkeypatch.setattr(twitter_client, "TwitterClient", FakeTwitterClient)
    monkeypatch.setattr(twitter_client, "_cffi_session", object())
    monkeypatch.setattr(XClient, "_build_transaction", lambda _self: None)
    monkeypatch.delattr(twitter_client, "_openbiliclaw_proxy", raising=False)
    monkeypatch.setenv("TWITTER_PROXY", "http://ambient:7890")
    network.set_outbound_proxy("socks5://custom:1080", mode="custom")

    XClient(cookie="auth_token=a; ct0=b")._client()

    assert seen == ["socks5://custom:1080"]
    assert os.environ["TWITTER_PROXY"] == "http://ambient:7890"
    assert twitter_client._cffi_session is None
    assert twitter_client._openbiliclaw_proxy == "socks5://custom:1080"
    network.reset_outbound_proxy_for_tests()


def test_xclient_direct_mode_clears_ambient_twitter_proxy_during_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from twitter_cli import client as twitter_client

    seen: list[str | None] = []

    class FakeTwitterClient:
        def __init__(self, _auth_token: str, _ct0: str) -> None:
            seen.append(os.environ.get("TWITTER_PROXY"))

    monkeypatch.setattr(twitter_client, "TwitterClient", FakeTwitterClient)
    monkeypatch.setattr(XClient, "_build_transaction", lambda _self: None)
    monkeypatch.delattr(twitter_client, "_openbiliclaw_proxy", raising=False)
    monkeypatch.setenv("TWITTER_PROXY", "http://ambient:7890")
    network.set_outbound_proxy("", mode="direct")

    XClient(cookie="auth_token=a; ct0=b")._client()

    assert seen == [None]
    assert os.environ["TWITTER_PROXY"] == "http://ambient:7890"


async def test_xclient_search_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_search", _fake_raw_search)
    out = await client.search("rust async", limit=5)
    assert out
    assert all(isinstance(t, dict) for t in out)
    # tweet_to_dict keys the tweet id under "id" (rest_id) and nests author.
    assert all("id" in t for t in out)
    assert out[0]["id"] == "1790000000000000001"
    assert out[0]["author"]["screenName"] == "handle"


async def test_xclient_search_honors_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def _many(query: str, *, count: int, product: str):
        return [_make_tweet(str(i)) for i in range(count + 10)]

    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_search", _many)
    out = await client.search("q", limit=3)
    assert len(out) == 3


async def test_xclient_for_you_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_for_you", lambda *, count: [_make_tweet("777")])
    out = await client.for_you(limit=5)
    assert out and out[0]["id"] == "777"


async def test_xclient_user_tweets_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_user_tweets", lambda handle, *, count: [_make_tweet("888")])
    out = await client.user_tweets("@handle", limit=5)
    assert out and out[0]["id"] == "888"


async def test_xclient_likes_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_likes", lambda *, count: [_make_tweet("555")])
    out = await client.likes(limit=5)
    assert out and out[0]["id"] == "555"
    assert out[0]["author"]["screenName"] == "handle"


async def test_xclient_likes_honors_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def _many(*, count: int):
        return [_make_tweet(str(i)) for i in range(count + 5)]

    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_likes", _many)
    out = await client.likes(limit=4)
    assert len(out) == 4


async def test_xclient_probe_reads_authenticated_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    client = XClient(cookie="auth_token=a; ct0=b")
    profile = object()
    monkeypatch.setattr(client, "_raw_me", lambda: profile)

    assert await client.probe() is profile


async def test_xclient_probe_maps_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from twitter_cli.client import TwitterAPIError

    client = XClient(cookie="auth_token=a; ct0=b")

    def _boom() -> object:
        raise TwitterAPIError(401, "unauthorized")

    monkeypatch.setattr(client, "_raw_me", _boom)
    with pytest.raises(XAuthError):
        await client.probe()


async def test_xclient_bookmarks_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_bookmarks", lambda *, count: [_make_tweet("666")])
    out = await client.bookmarks(limit=5)
    assert out and out[0]["id"] == "666"


async def test_xclient_likes_maps_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired cookie on the likes path surfaces as XAuthError (→ expired_cookie)."""
    from twitter_cli.client import TwitterAPIError

    def _boom(*, count: int):
        raise TwitterAPIError(401, "unauthorized")

    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_likes", _boom)
    with pytest.raises(XAuthError):
        await client.likes(limit=5)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, XAuthError),
        (403, XBlockedError),
        (429, XRateLimitError),
    ],
)
async def test_search_maps_api_errors(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected: type[Exception]
) -> None:
    from twitter_cli.client import TwitterAPIError

    def _boom(query: str, *, count: int, product: str):
        raise TwitterAPIError(status_code, "nope")

    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_search", _boom)
    with pytest.raises(expected):
        await client.search("q", limit=5)


async def test_authentication_error_maps_to_xauth(monkeypatch: pytest.MonkeyPatch) -> None:
    from twitter_cli.exceptions import AuthenticationError

    def _boom(query: str, *, count: int, product: str):
        raise AuthenticationError("expired")

    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_raw_search", _boom)
    with pytest.raises(XAuthError):
        await client.search("q", limit=5)


# ── x-client-transaction-id seeding (issue: X SearchTimeline 404) ──────────
# All offline: TwitterClient and the homepage fetch are stubbed.


def test_client_seeds_transaction_onto_twitter_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_client`` injects the bootstrapped generator and skips the broken one."""
    import twitter_cli.client as tcc

    captured: dict[str, object] = {}

    class _FakeTwitterClient:
        def __init__(self, auth_token: str, ct0: str) -> None:
            captured["auth"] = (auth_token, ct0)

    monkeypatch.setattr(tcc, "TwitterClient", _FakeTwitterClient)
    sentinel = object()
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_build_transaction", lambda: sentinel)

    built = client._client()
    assert built._client_transaction is sentinel
    assert built._ct_init_attempted is True
    assert captured["auth"] == ("a", "b")


def test_client_leaves_client_untouched_when_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed bootstrap (``None``) must not set the transaction attributes."""
    import twitter_cli.client as tcc

    class _FakeTwitterClient:
        def __init__(self, auth_token: str, ct0: str) -> None:
            pass

    monkeypatch.setattr(tcc, "TwitterClient", _FakeTwitterClient)
    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_build_transaction", lambda: None)

    built = client._client()
    assert not hasattr(built, "_client_transaction")
    assert not hasattr(built, "_ct_init_attempted")


def test_transaction_built_once_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful transaction is reused for the client's lifetime."""
    calls = {"n": 0}

    def _build() -> object:
        calls["n"] += 1
        return object()

    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_build_transaction", _build)

    first = client._ensure_transaction()
    second = client._ensure_transaction()
    assert first is second
    assert calls["n"] == 1


def test_failed_transaction_cached_as_none_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed build caches ``None`` so it is not re-attempted every call."""
    calls = {"n": 0}

    def _build() -> object | None:
        calls["n"] += 1
        return None

    client = XClient(cookie="auth_token=a; ct0=b")
    monkeypatch.setattr(client, "_build_transaction", _build)

    assert client._ensure_transaction() is None
    assert client._ensure_transaction() is None
    assert calls["n"] == 1


def test_build_transaction_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any failure during bootstrap returns ``None`` instead of raising."""
    import twitter_cli.client as tcc

    def _boom() -> object:
        raise RuntimeError("network down")

    monkeypatch.setattr(tcc, "_get_cffi_session", _boom)
    client = XClient(cookie="auth_token=a; ct0=b")
    assert client._build_transaction() is None
