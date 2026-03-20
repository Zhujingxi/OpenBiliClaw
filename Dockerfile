FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md config.example.toml ./
COPY src ./src

RUN pip install .

EXPOSE 8420

CMD ["python", "-m", "openbiliclaw.docker_runtime", "openbiliclaw", "serve-api", "--host", "0.0.0.0", "--port", "8420"]
