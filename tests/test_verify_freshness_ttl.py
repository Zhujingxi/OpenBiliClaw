"""The user-visible freshness window is not the probe-reuse throttle.

One constant used to serve both: ``PROBE_OK_TTL_SECONDS = 60``. That is right
for throttling outbound probes (a settings page polling every 30s must not fire
a real request; 抖音's per-``msToken`` cookie churn must not storm the platform)
but wrong as the "验证已过期" window — a user who clicked 测试连接 watched it
flip to expired inside a minute, reading as a problem where a one-minute-old
login is perfectly fresh. These tests pin the two apart so a later edit can't
quietly re-merge them (CLAUDE.md pitfall #3).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from openbiliclaw.api.source_auth import probe_cache
from openbiliclaw.api.source_auth.probe_cache import PROBE_OK_TTL_SECONDS, LiveProbeCache
from openbiliclaw.api.source_auth.providers import (
    _VERIFIED_FRESH_SECONDS,
    SourceAuthContext,
    _probe_ttl,
    _probe_verdict,
)
from openbiliclaw.api.source_auth.write import credential_fingerprint

if TYPE_CHECKING:
    import pytest


def test_visible_freshness_is_far_longer_than_the_probe_reuse_window() -> None:
    # If these ever collapse back to one number, the badge regresses to
    # expiring within the probe-throttle window.
    assert _VERIFIED_FRESH_SECONDS > PROBE_OK_TTL_SECONDS
    assert _VERIFIED_FRESH_SECONDS >= 3600, "a verify must stay fresh on a human timescale"


def test_probe_reuse_window_stays_short() -> None:
    # The throttle is a risk-control guard; lengthening it would let a stale
    # verdict authorise a write. Kept explicit so nobody widens it by reflex.
    assert PROBE_OK_TTL_SECONDS == 60


def test_verified_uses_the_visible_window_failed_stays_short() -> None:
    # A good verdict rides the long window; a repaired credential must still
    # turn green promptly, so failure keeps the short re-check window.
    assert _probe_ttl("verified") == _VERIFIED_FRESH_SECONDS
    assert _probe_ttl("failed") < PROBE_OK_TTL_SECONDS


def test_provider_keeps_success_visible_after_probe_reuse_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 10_000.0
    monkeypatch.setattr(probe_cache.time, "monotonic", lambda: now)
    cookie = "SESSDATA=session; bili_jct=csrf; DedeUserID=1"
    probes = LiveProbeCache()
    probes.record(
        "bilibili",
        authenticated=True,
        fingerprint=credential_fingerprint("bilibili", cookie),
    )
    ctx = SourceAuthContext(
        cfg=SimpleNamespace(sources=SimpleNamespace()),
        database=SimpleNamespace(),
        probes=probes,
    )

    now += PROBE_OK_TTL_SECONDS + 1
    assert _probe_verdict(ctx, "bilibili", credential="cookie", cookie=cookie)[0] == "verified"

    now += _VERIFIED_FRESH_SECONDS
    assert _probe_verdict(ctx, "bilibili", credential="cookie", cookie=cookie)[0] == "stale"
