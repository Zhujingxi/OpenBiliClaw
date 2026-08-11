"""Repository-wide test safety defaults."""

from pydantic_ai import models

# Real model access is opt-in. Integration tests must explicitly enable it in
# their own fixture after carrying the integration marker.
models.ALLOW_MODEL_REQUESTS = False
