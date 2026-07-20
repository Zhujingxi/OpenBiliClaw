"""Extracted API route modules.

Each submodule exposes a factory function ``build_<name>_router(deps)`` that
returns an ``APIRouter`` configured from a narrow dependency bundle. The
composition root (``create_app`` in ``api/app.py``) constructs the bundles
from existing closures and includes the resulting routers, preserving route
registration order and externally visible behavior.
"""
