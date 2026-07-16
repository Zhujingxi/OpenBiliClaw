"""Feature-owned v1 HTTP routers."""

from openbiliclaw.api.routers import (
    chat,
    events,
    feed,
    interactions,
    jobs,
    library,
    onboarding,
    profile,
    settings,
    source_tasks,
    sources,
    system,
)

ROUTERS = (
    system.router,
    settings.router,
    onboarding.router,
    sources.router,
    source_tasks.router,
    events.router,
    profile.router,
    feed.router,
    interactions.router,
    library.router,
    chat.router,
    jobs.router,
)

__all__ = ["ROUTERS"]
