"""First-party Weibo provider."""

from .capabilities import WeiboProvider
from .client import WeiboClient
from .manifest import WEIBO_MANIFEST

__all__ = ["WEIBO_MANIFEST", "WeiboClient", "WeiboProvider"]
