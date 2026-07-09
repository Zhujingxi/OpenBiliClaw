"""Unit tests for the stdlib-only password-gate primitives (auth_core)."""

from __future__ import annotations

from openbiliclaw import auth_core as ac


def test_extension_access_key_generation_stores_only_digest() -> None:
    key_id, full_key, record = ac.generate_extension_access_key()

    assert full_key.startswith(f"obc_ext_{key_id}.")
    assert full_key not in record
    assert record.startswith(f"{key_id}:")
    assert ac.verify_extension_access_key(full_key, [record]) is True
    assert ac.extension_access_key_ids([record]) == [key_id]


def test_extension_access_key_rejects_malformed_unknown_and_wrong_secret() -> None:
    key_id, full_key, record = ac.generate_extension_access_key()

    assert ac.parse_extension_access_key("not-a-key") is None
    assert ac.verify_extension_access_key("not-a-key", [record]) is False
    assert ac.verify_extension_access_key(full_key.replace(key_id, "f" * 12), [record]) is False
    assert ac.verify_extension_access_key(full_key + "x", [record]) is False


def test_extension_origins_recognize_mixed_case_schemes() -> None:
    extension_id = "abcdefghijklmnop"
    for origin in (
        f"Chrome-Extension://{extension_id}",
        f"mOz-ExTeNsIoN://{extension_id}",
    ):
        assert ac.is_extension_origin(origin) is True


def test_trusted_local_ips_are_loopback_only() -> None:
    assert {"127.0.0.1", "::1"} == ac._TRUSTED_LOCAL_IPS


def test_password_hash_roundtrip_and_salting() -> None:
    h1 = ac.hash_password("hunter2")
    h2 = ac.hash_password("hunter2")
    assert h1 != h2  # random salt
    assert ac.verify_password("hunter2", h1)
    assert ac.verify_password("hunter2", h2)
    assert not ac.verify_password("wrong", h1)
    assert not ac.verify_password("hunter2", "not-a-hash")


def test_token_sign_verify_never_expire() -> None:
    secret = "s3cr3t"
    token = ac.sign_token(secret, epoch=0, ttl_hours=0, now=1000)
    assert ac.token_expires_at(token) is None
    assert ac.verify_token(token, secret, current_epoch=0, now=10**9)  # far future, no exp
    assert not ac.verify_token(token, "other-secret", current_epoch=0, now=1000)


def test_token_expiry_and_epoch_revocation() -> None:
    secret = "s3cr3t"
    token = ac.sign_token(secret, epoch=5, ttl_hours=1, now=1000)
    assert ac.token_expires_at(token) == 1000 + 3600
    assert ac.verify_token(token, secret, current_epoch=5, now=2000)
    # expired
    assert not ac.verify_token(token, secret, current_epoch=5, now=1000 + 3600)
    # revoked: epoch advanced beyond the token's ep
    assert not ac.verify_token(token, secret, current_epoch=6, now=2000)


def test_same_second_revocation_via_monotonic_epoch() -> None:
    # A token minted in the same second as a revoke must still die (no second-grained hole).
    secret = "s3cr3t"
    token = ac.sign_token(secret, epoch=0, ttl_hours=0, now=1000)
    assert ac.verify_token(token, secret, current_epoch=0, now=1000)
    assert not ac.verify_token(token, secret, current_epoch=1, now=1000)


def test_password_fingerprint_is_stable_over_plaintext_not_salted_hash() -> None:
    secret = "sign-key"
    # same plaintext, different (re-salted) hashes -> identical fingerprint
    fp_a = ac.password_fingerprint(secret, plain="pw", password_hash=ac.hash_password("pw"))
    fp_b = ac.password_fingerprint(secret, plain="pw", password_hash=ac.hash_password("pw"))
    assert fp_a == fp_b
    # different plaintext -> different fingerprint
    assert fp_a != ac.password_fingerprint(secret, plain="other", password_hash="x")
    # hash-only path is stable for the same supplied hash
    assert ac.password_fingerprint(
        secret, plain=None, password_hash="H"
    ) == ac.password_fingerprint(secret, plain=None, password_hash="H")
    # different signing secret -> different fingerprint
    assert fp_a != ac.password_fingerprint("other-key", plain="pw", password_hash="x")


def test_norm_ip_variants() -> None:
    assert ac.norm_ip("127.0.0.1") == "127.0.0.1"
    assert ac.norm_ip("203.0.113.9:8080") == "203.0.113.9"
    assert ac.norm_ip("[::1]") == "::1"
    assert ac.norm_ip("[::1]:5678") == "::1"
    assert ac.norm_ip("fe80::1%eth0") == "fe80::1"
    assert ac.norm_ip("garbage") is None
    assert ac.norm_ip("") is None
    assert ac.norm_ip(None) is None


def test_resolve_client_ip_direct() -> None:
    ip, local = ac.resolve_client_ip(
        "127.0.0.1", xff_values=[], has_forward_header=False, trusted_proxies=[]
    )
    assert ip == "127.0.0.1"
    assert local is True
    assert ac.is_trusted_local(ip, local)


def test_resolve_client_ip_forward_header_from_untrusted_peer_is_remote() -> None:
    ip, local = ac.resolve_client_ip(
        "127.0.0.1", xff_values=["10.0.0.5"], has_forward_header=True, trusted_proxies=[]
    )
    # loopback peer but carrying a forward header from a non-trusted hop -> not local
    assert local is False
    assert not ac.is_trusted_local(ip, local)


def test_resolve_client_ip_spoofed_leftmost_loopback_is_rejected() -> None:
    # attacker sends "X-Forwarded-For: 127.0.0.1"; trusted proxy appends real IP
    ip, local = ac.resolve_client_ip(
        "192.168.1.5",
        xff_values=["127.0.0.1, 203.0.113.9"],
        has_forward_header=True,
        trusted_proxies=["192.168.1.5"],
    )
    assert ip == "203.0.113.9"
    assert not ac.is_trusted_local(ip, local)  # rightmost-untrusted wins, not the spoofed left


def test_resolve_client_ip_genuine_local_behind_trusted_proxy() -> None:
    ip, local = ac.resolve_client_ip(
        "192.168.1.5",
        xff_values=["127.0.0.1"],
        has_forward_header=True,
        trusted_proxies=["192.168.1.5"],
    )
    assert ip == "127.0.0.1"
    assert ac.is_trusted_local(ip, local)


def test_resolve_client_ip_malformed_xff_fails_closed() -> None:
    ip, local = ac.resolve_client_ip(
        "192.168.1.5",
        xff_values=["not-an-ip"],
        has_forward_header=True,
        trusted_proxies=["192.168.1.5"],
    )
    assert local is False


def test_effective_origin_and_same_origin_normalization() -> None:
    eff = ac.effective_scheme_host(
        url_scheme="http",
        host_header="testserver",
        xf_proto=None,
        xf_host=None,
        peer="192.168.1.5",
        trusted_proxies=[],
    )
    assert eff == ("http", "testserver", 80)
    assert ac.same_origin(ac.parse_origin("http://testserver"), eff)
    assert ac.same_origin(ac.parse_origin("http://testserver:80"), eff)  # default port elided
    assert ac.same_origin(ac.parse_origin("HTTP://TestServer"), eff)  # case-insensitive
    assert not ac.same_origin(ac.parse_origin("http://evil.example"), eff)
    assert not ac.same_origin(ac.parse_origin("https://testserver"), eff)  # scheme differs
    assert not ac.same_origin(None, eff)  # missing origin is not same-origin


def test_effective_scheme_honours_xfproto_only_from_trusted_proxy() -> None:
    trusted = ac.effective_scheme_host(
        url_scheme="http",
        host_header="testserver",
        xf_proto="https",
        xf_host=None,
        peer="192.168.1.5",
        trusted_proxies=["192.168.1.5"],
    )
    assert trusted is not None and trusted[0] == "https"
    spoofed = ac.effective_scheme_host(
        url_scheme="http",
        host_header="testserver",
        xf_proto="https",
        xf_host=None,
        peer="203.0.113.1",  # not a trusted proxy
        trusted_proxies=["192.168.1.5"],
    )
    assert spoofed is not None and spoofed[0] == "http"  # spoofed XFP ignored


def test_origin_allowed_for_bearer() -> None:
    allowed = ["http://desktop.local:3000"]
    assert ac.origin_allowed_for_bearer("http://desktop.local:3000", allowed)
    assert not ac.origin_allowed_for_bearer("http://desktop.local", allowed)  # port differs
    assert not ac.origin_allowed_for_bearer(None, allowed)
    assert not ac.origin_allowed_for_bearer("http://evil.example", allowed)
