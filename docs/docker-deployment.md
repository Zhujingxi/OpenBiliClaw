# Docker deployment

Docker Compose is the primary supported deployment method.

## First start

Create a model-key file outside the repository (or use another ignored path) and point Compose at it:

```bash
install -m 600 /dev/null "$HOME/.config/openbiliclaw/model_api_key"
printf '%s' 'YOUR_TEST_OR_PRODUCTION_KEY' > "$HOME/.config/openbiliclaw/model_api_key"
export OPENBILICLAW_MODEL_KEY_FILE="$HOME/.config/openbiliclaw/model_api_key"
docker compose up -d --build
```

Compose builds two separate images:

- `openbiliclaw-backend`: API plus the built Vue SPA. It never serves a model.
- `embedding`: Infinity 0.0.77 with only `infinity_emb[torch,server]`, serving `BAAI/bge-small-zh-v1.5` on the private Compose network. The model cache is persisted in `embedding_models`.

The backend waits for the embedding healthcheck. On its first start, `docker/seed-runtime.py` reads the runtime-mounted model key, stores it in the credential vault, generates a random API bearer, stores that separately in the vault, and writes only opaque references to `/app/runtime/config.toml`. Neither secret is baked into an image, Compose config, nor environment variable. Existing runtime configuration is never overwritten.

The chat model is configured in `config.docker.toml` by models.dev catalog id (default `kimi-for-coding`); endpoint, wire protocol, and capabilities resolve from the catalog at startup, so the container needs network access to models.dev on first start (cached in the runtime volume afterwards). The `[embedding]` section bypasses the catalog: it uses the OpenAI-native embedding transport against the bundled sidecar.

The API listens on host port 8420 (override with `OPENBILICLAW_API_PORT`) and health is `GET /v1/runtime/health`. The backend container's authenticated healthcheck resolves the bearer internally; `docker compose ps` reports healthy only after the API starts.

## Bearer authentication

Docker binds the backend to `0.0.0.0`, so the API fails closed unless a bearer is configured. The first-start seeder generates one rather than accepting a plaintext environment variable. Retrieve it locally when enrolling a client:

```bash
docker compose exec -T openbiliclaw-backend python -c \
'import tomllib; from pathlib import Path; from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend; from openbiliclaw.infrastructure.credentials.vault import CredentialVault; c=tomllib.load(open("/app/runtime/config.toml","rb")); r=c["host"]["bearer_secret_ref"].removeprefix("vault:"); CredentialVault(ProtectedFileBackend(Path("/app/runtime/credentials.json"))).resolve(r, lambda s: print(bytes(s).decode()))'
```

Treat that command's output as a password. Send it as `Authorization: Bearer <token>`. Mutations additionally require matching `X-Device-ID` and `X-CSRF-Token` headers. The browser extension already has a bearer-token enrollment field. The current Vue Web UI has no bearer enrollment/storage plumbing, so opening the Docker-hosted SPA directly returns 401; L7 will test and address that presentation gap rather than weakening the backend boundary.

## Persistence and restart behavior

Compose persists `/app/runtime`, database data, logs, and the embedding model cache in named volumes. Restarting or rebuilding retains configuration, vault credentials, observations, profile state, recommendation state, and feedback.

Access handles are intentionally process-local. The vault currently has no durable provider/account-to-credential-reference mapping, so a Bilibili authenticated connection is **disconnected after container restart** even though its opaque credential remains in the vault. The client must resubmit its provider form (the `/v1/sources/connect` `submission.cookie` field) after restart. Anonymous access must likewise reconnect. Docker does not silently invent a durable reconnection mapping.

## Verification and teardown

```bash
docker compose ps
docker compose exec -T openbiliclaw-backend \
  openbiliclaw check --config /app/runtime/config.toml --data-dir /app/runtime
# Keep volumes:
docker compose down
# Delete all deployment data:
docker compose down -v
```

The first embedding start downloads roughly 100 MB of model weights; image layers are larger because CPU Torch is included. The verified 20-candidate Bilibili refill completed under the supervised 55-second production job timeout on the tested host, but slower networks/CPUs may exceed that fixed policy and should monitor `/v1/runtime/health` job results.
