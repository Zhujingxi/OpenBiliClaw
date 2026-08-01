# syntax=docker/dockerfile:1
# OpenBiliClaw TLS reverse proxy.
#
# Terminates TLS on :8443 with the project's self-signed certificate and
# forwards traffic to openbiliclaw-backend over the internal Docker network.
# Self-contained: the image only contains this single Python script; the
# certificate/key/CRL live in a persistent runtime volume (default /certs).
# All runtime knobs are environment variables, so the container starts with
# no arguments.
FROM python:3.11-slim

# Certificate directory inside the container (where the persistent cert volume
# is mounted). Override at build time with --build-arg CERT_DIR=/path; still
# overridable at runtime via CERT_FILE/KEY_FILE/CRL_FILE.
ARG CERT_DIR=/certs

LABEL org.opencontainers.image.source="https://github.com/whiteguo233/OpenBiliClaw" \
      org.opencontainers.image.description="OpenBiliClaw TLS reverse proxy — terminates TLS and normalises Origin for the backend" \
      org.opencontainers.image.licenses="MIT"

ENV CERT_DIR=${CERT_DIR} \
    CERT_FILE=${CERT_DIR}/srv.crt \
    KEY_FILE=${CERT_DIR}/srv.key \
    CRL_FILE=${CERT_DIR}/ca.crl

WORKDIR /app

# cryptography is used for on-startup auto-generation of the self-signed
# certificate chain when the cert volume is empty (new deployments).
RUN pip install --no-cache-dir "cryptography>=41"

COPY src/openbiliclaw/tls_proxy.py /app/openbiliclaw_tls_proxy.py

EXPOSE 8443

# Healthcheck hits a certificate-independent endpoint over TLS so a valid
# user-supplied cert does not require a local CA/CRL file just to become healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,ssl,urllib.request,sys; port=os.environ.get('LISTEN_PORT','8443'); ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; sys.exit(0 if urllib.request.urlopen(f'https://127.0.0.1:{port}/healthz', context=ctx, timeout=4).status==200 else 1)"

CMD ["python", "/app/openbiliclaw_tls_proxy.py"]
