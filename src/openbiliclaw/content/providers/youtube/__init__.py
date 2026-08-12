"""First-party YouTube provider."""

from .capabilities import YouTubeProvider
from .client import HttpxYouTubeTransport, YouTubeClient, YouTubeTransport
from .manifest import YOUTUBE_MANIFEST
from .takeout import TakeoutEvent, TakeoutParseResult, TakeoutStats, parse_takeout

__all__ = [
    "YOUTUBE_MANIFEST",
    "TakeoutEvent",
    "TakeoutParseResult",
    "TakeoutStats",
    "HttpxYouTubeTransport",
    "YouTubeClient",
    "YouTubeProvider",
    "YouTubeTransport",
    "parse_takeout",
]
