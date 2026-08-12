# Docker deployment

```bash
docker compose up -d --build
```

The image and Compose service invoke the sole supported command:

```text
openbiliclaw serve --data-dir /app/runtime
```

The API listens on port 8420 and health is `GET /v1/runtime/health`. The built Vue SPA is copied into the image and served by the same FastAPI host. Persist `/app/runtime`; target schema migration refuses unversioned application tables and never silently resets them.

Set `OPENBILICLAW_API_HOST=0.0.0.0` only inside the container/network boundary. Non-loopback host configuration must also provide the host security bearer policy when constructed outside the reviewed Compose setup.
