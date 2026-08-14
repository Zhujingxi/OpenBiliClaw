"""First-party YouTube provider."""

from .capabilities import YouTubeProvider
from .client import YouTubeClient, YouTubeTransport, YtDlpYouTubeTransport
from .manifest import YOUTUBE_MANIFEST
from .takeout import TakeoutEvent, TakeoutParseResult, TakeoutStats, parse_takeout

__all__ = [
    "YOUTUBE_MANIFEST",
    "TakeoutEvent",
    "TakeoutParseResult",
    "TakeoutStats",
    "YtDlpYouTubeTransport",
    "YouTubeClient",
    "YouTubeProvider",
    "YouTubeTransport",
    "parse_takeout",
]
