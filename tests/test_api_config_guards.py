from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.config import (
    load_config as load_config_from_path,
)
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    ModelConfig,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_client(
    monkeypatch,
    tmp_path: Path,
    initial_cfg: Config,
) -> tuple[TestClient, Config, Path]:
    config_path = tmp_path / "config.toml"
    save_config(initial_cfg, config_path)

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: initial_cfg)
    monkeypatch.setattr(
        "openbiliclaw.config.save_config",
        lambda cfg, path=None: save_config(cfg, config_path),
    )

    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app), initial_cfg, config_path


def _base_config() -> Config:
    return Config(
        models=ModelConfig(
            chat=ChatRouteConfig(
                connections=(
                    ChatConnection(
                        id="openai-main",
                        name="OpenAI",
                        type="openai_compatible",
                        preset="openai",
                        model="gpt-4o-mini",
                        base_url="https://api.openai.com/v1",
                        credential=CredentialConfig(source="inline", value="sk-real-key"),
                        api_mode="chat_completions",
                    ),
                    ChatConnection(
                        id="deepseek-backup",
                        name="DeepSeek",
                        type="openai_compatible",
                        preset="deepseek",
                        model="deepseek-chat",
                        base_url="https://api.deepseek.com",
                        credential=CredentialConfig(source="inline", value="deepseek-key"),
                        api_mode="chat_completions",
                    ),
                )
            ),
            embedding=EmbeddingRouteConfig(
                enabled=True,
                settings=EmbeddingModelSettings(model="text-embedding-3-small"),
                providers=(
                    EmbeddingProviderConfig(
                        id="openai-embedding",
                        name="OpenAI embedding",
                        type="openai_compatible",
                        preset="openai",
                        base_url="https://api.openai.com/v1",
                        credential=CredentialConfig(source="inline", value="embed-key"),
                    ),
                ),
            ),
        )
    )


@pytest.mark.parametrize(
    "legacy_update",
    [
        {"openai": {"api_key": "sk-new", "model": "gpt-new"}},
        {"fallback_provider": "claude"},
        {"embedding": {"provider": "ollama", "model": "other"}},
    ],
)
def test_put_config_ignores_all_legacy_model_updates(
    monkeypatch,
    tmp_path,
    legacy_update,
) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    before = tomllib.loads(config_path.read_text(encoding="utf-8"))["models"]

    response = client.put("/api/config", json={"llm": legacy_update, "language": "en"})

    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == ["model_config_not_updated"]
    rendered = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert rendered["models"] == before
    assert rendered["general"]["language"] == "en"


def test_put_config_ignores_legacy_model_reset(
    monkeypatch,
    tmp_path,
) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    before = tomllib.loads(config_path.read_text(encoding="utf-8"))["models"]

    response = client.put(
        "/api/config",
        json={"reset_fields": ["llm.openai.api_key"]},
    )

    assert response.status_code == 200
    assert response.json()["warnings"] == ["model_config_not_updated"]
    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["models"] == before


def test_get_config_returns_secret_free_read_only_legacy_projection(
    monkeypatch,
    tmp_path,
) -> None:
    client, _cfg, _config_path = _make_client(monkeypatch, tmp_path, _base_config())

    ordinary = client.get("/api/config").json()["llm"]
    revealed = client.get("/api/config", params={"reveal_keys": "true"}).json()["llm"]

    assert ordinary["read_only"] is True
    assert ordinary["authoritative"] is False
    assert ordinary["default_provider"] == "openai"
    assert ordinary["fallback_provider"] == "deepseek"
    assert ordinary["embedding"]["provider"] == "openai"
    assert ordinary["openai"]["api_key"] == ""
    assert revealed["openai"]["api_key"] == ""
    assert revealed["embedding"]["api_key"] == ""


def test_put_config_unknown_non_model_reset_is_rejected_without_mutation(
    monkeypatch,
    tmp_path,
) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    before = config_path.read_bytes()

    response = client.put("/api/config", json={"reset_fields": ["storage.db_path"]})

    assert response.status_code == 400
    assert config_path.read_bytes() == before


# ── Source cookie guards (bilibili masked/empty echo; dy/x file routing) ──


def _cookie_config(tmp_path: Path) -> Config:
    from openbiliclaw.config import BilibiliConfig

    cfg = _base_config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.bilibili = BilibiliConfig(
        auth_method="cookie",
        cookie="SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42",
    )
    return cfg


def test_put_config_ignores_masked_bilibili_cookie_echo(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put(
        "/api/config",
        json={"bilibili": {"cookie": "SESS************ID=42"}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).bilibili.cookie == (
        "SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42"
    )


def test_put_config_ignores_empty_bilibili_cookie(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put("/api/config", json={"bilibili": {"cookie": ""}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).bilibili.cookie == (
        "SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42"
    )


def test_put_config_writes_real_new_bilibili_cookie(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put(
        "/api/config",
        json={"bilibili": {"cookie": "SESSDATA=new-sess; bili_jct=new-csrf; DedeUserID=43"}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).bilibili.cookie == (
        "SESSDATA=new-sess; bili_jct=new-csrf; DedeUserID=43"
    )


def test_put_config_routes_douyin_cookie_to_data_file(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.douyin_auth import DouyinCookieManager

    monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, cfg)

    response = client.put(
        "/api/config",
        json={"sources": {"douyin": {"cookie": "sessionid=dy-sess; ttwid=dy-tw"}}},
    )

    assert response.status_code == 200
    # Secret lands in data/douyin_cookie.json, never in config.toml.
    assert DouyinCookieManager(cfg.data_path).load_cookie() == "sessionid=dy-sess; ttwid=dy-tw"
    assert "dy-sess" not in config_path.read_text(encoding="utf-8")


def test_put_config_routes_x_cookie_to_data_file(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.x_auth import XCookieManager

    monkeypatch.delenv("OPENBILICLAW_X_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, cfg)

    response = client.put(
        "/api/config",
        json={"sources": {"twitter": {"cookie": "auth_token=x-at; ct0=x-csrf"}}},
    )

    assert response.status_code == 200
    assert XCookieManager(cfg.data_path).load_cookie() == "auth_token=x-at; ct0=x-csrf"
    assert "x-at" not in config_path.read_text(encoding="utf-8")


def test_put_config_ignores_masked_douyin_cookie_echo(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.douyin_auth import DouyinCookieManager

    monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    manager = DouyinCookieManager(cfg.data_path)
    manager.set_cookie("sessionid=dy-real", source="test")
    client, _cfg, _config_path = _make_client(monkeypatch, tmp_path, cfg)

    response = client.put(
        "/api/config",
        json={"sources": {"douyin": {"cookie": "sess************real"}}},
    )

    assert response.status_code == 200
    assert manager.load_cookie() == "sessionid=dy-real"


def test_put_config_empty_cookie_env_keeps_existing_name(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put(
        "/api/config",
        json={
            "sources": {
                "douyin": {"cookie_env": ""},
                "twitter": {"cookie_env": ""},
            }
        },
    )

    assert response.status_code == 200
    saved = load_config_from_path(config_path)
    assert saved.sources.douyin.cookie_env == "OPENBILICLAW_DOUYIN_COOKIE"
    assert saved.sources.twitter.cookie_env == "OPENBILICLAW_X_COOKIE"


def test_get_config_exposes_douyin_and_x_cookies_like_bilibili(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.douyin_auth import DouyinCookieManager
    from openbiliclaw.sources.x_auth import XCookieManager

    monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
    monkeypatch.delenv("OPENBILICLAW_X_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    DouyinCookieManager(cfg.data_path).set_cookie(
        "sessionid=dy-sess-1234567890; ttwid=dy-tw", source="test"
    )
    XCookieManager(cfg.data_path).set_cookie(
        "auth_token=x-at-1234567890; ct0=x-csrf", source="test"
    )
    client, _cfg, _config_path = _make_client(monkeypatch, tmp_path, cfg)

    masked = client.get("/api/config").json()
    assert "****" in masked["sources"]["douyin"]["cookie"]
    assert "dy-sess-1234567890" not in masked["sources"]["douyin"]["cookie"]
    assert "****" in masked["sources"]["twitter"]["cookie"]
    assert "****" in masked["bilibili"]["cookie"]

    revealed = client.get("/api/config?reveal_keys=true").json()
    assert revealed["sources"]["douyin"]["cookie"] == "sessionid=dy-sess-1234567890; ttwid=dy-tw"
    assert revealed["sources"]["twitter"]["cookie"] == "auth_token=x-at-1234567890; ct0=x-csrf"
    assert revealed["bilibili"]["cookie"] == (
        "SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42"
    )


# ── [network].proxy API exposure ────────────────────────────────────────────


def _proxy_config(proxy: str) -> Config:
    cfg = _base_config()
    cfg.network.mode = "custom" if proxy else "direct"
    cfg.network.proxy = proxy
    return cfg


def test_get_config_exposes_network_proxy(monkeypatch, tmp_path) -> None:
    client, _cfg, _path = _make_client(
        monkeypatch, tmp_path, _proxy_config("socks5://127.0.0.1:1080")
    )
    body = client.get("/api/config").json()
    assert body["network"]["mode"] == "custom"
    assert body["network"]["proxy"] == "socks5://127.0.0.1:1080"


def test_get_config_masks_proxy_userinfo(monkeypatch, tmp_path) -> None:
    client, _cfg, _path = _make_client(
        monkeypatch, tmp_path, _proxy_config("socks5://user:secret@127.0.0.1:1080")
    )
    body = client.get("/api/config").json()
    assert "secret" not in body["network"]["proxy"]
    assert body["network"]["proxy"] == "socks5://***@127.0.0.1:1080"


def test_put_config_writes_valid_network_proxy(monkeypatch, tmp_path) -> None:
    from openbiliclaw import network

    network.reset_outbound_proxy_for_tests()
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"network": {"proxy": "socks5://127.0.0.1:1080"}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).network.proxy == "socks5://127.0.0.1:1080"
    # Hot path updated the process-level source of truth.
    assert network.outbound_proxy_mode() == "custom"
    assert network.outbound_proxy_url() == "socks5://127.0.0.1:1080"
    network.reset_outbound_proxy_for_tests()


def test_put_config_rejects_invalid_network_proxy(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    before = config_path.read_text(encoding="utf-8")

    response = client.put("/api/config", json={"network": {"proxy": "ftp://127.0.0.1:1"}})

    assert response.status_code == 400
    # config.toml is untouched on rejection.
    assert config_path.read_text(encoding="utf-8") == before


def test_put_config_switches_to_direct_and_ignores_environment_proxy(monkeypatch, tmp_path) -> None:
    from openbiliclaw import network

    client, _cfg, config_path = _make_client(
        monkeypatch, tmp_path, _proxy_config("http://127.0.0.1:7897")
    )

    response = client.put("/api/config", json={"network": {"mode": "direct"}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).network.mode == "direct"
    assert network.outbound_httpx_kwargs() == {"trust_env": False}


def test_put_config_rejects_custom_mode_without_proxy(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    before = config_path.read_text(encoding="utf-8")

    response = client.put("/api/config", json={"network": {"mode": "custom", "proxy": ""}})

    assert response.status_code == 400
    assert config_path.read_text(encoding="utf-8") == before


def test_put_config_ignores_masked_proxy_echo(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(
        monkeypatch, tmp_path, _proxy_config("socks5://user:secret@127.0.0.1:1080")
    )

    response = client.put("/api/config", json={"network": {"proxy": "socks5://***@127.0.0.1:1080"}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).network.proxy == "socks5://user:secret@127.0.0.1:1080"
