"""Static contracts for the supported Docker deployment."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_compose_keeps_model_serving_in_a_healthchecked_sidecar() -> None:
    compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    sidecar = (_ROOT / "docker/infinity-emb.Dockerfile").read_text(encoding="utf-8")

    assert "  embedding:" in compose
    assert "condition: service_healthy" in compose
    assert "http://127.0.0.1:7997/health" in compose
    assert "embedding_models:/models" in compose
    assert "infinity_emb[torch,server]==0.0.77" in sidecar
    assert "infinity_emb[all]" not in sidecar
    assert "pip install" in sidecar and "optimum" not in sidecar.split("RUN pip install", 1)[1]
    assert "infinity_emb" not in (_ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_prebuilt_compose_keeps_sidecar_and_runtime_secret_contract() -> None:
    compose = (_ROOT / "docker-compose.prebuilt.yml").read_text(encoding="utf-8")

    assert "  embedding:" in compose
    assert "infinity_emb[torch,server]==0.0.77" in compose
    assert "--no-bettertransformer" in compose
    assert "condition: service_healthy" in compose
    assert "OPENBILICLAW_MODEL_KEY_FILE: /run/secrets/model_api_key" in compose
    assert 'python", "/app/docker/healthcheck.py' in compose


def test_build_context_excludes_every_supported_secret_location() -> None:
    ignored = (_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "data-e2e/" in ignored
    assert "model_api_key.txt" in ignored


def test_compose_mounts_runtime_secret_instead_of_baking_it() -> None:
    compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    template = (_ROOT / "config.docker.toml").read_text(encoding="utf-8")

    assert "OPENBILICLAW_MODEL_KEY_FILE: /run/secrets/model_api_key" in compose
    assert "file: ${OPENBILICLAW_MODEL_KEY_FILE" in compose
    assert "python /app/docker/seed-runtime.py" in dockerfile
    assert 'secret_ref = "vault:DOCKER_MODEL_SECRET_REF"' in template
    assert 'bearer_secret_ref = "vault:DOCKER_BEARER_SECRET_REF"' in template
    assert 'endpoint = "http://embedding:7997"' in template
    assert "kimi_api_key" not in dockerfile
