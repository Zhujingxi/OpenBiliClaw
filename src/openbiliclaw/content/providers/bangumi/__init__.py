"""First-party Bangumi provider."""

from .auth import BANGUMI_CONNECTION_FORM, BangumiCredentialVerifier
from .capabilities import BangumiProvider
from .client import BangumiClient, BangumiTransport, HttpxBangumiTransport
from .manifest import BANGUMI_MANIFEST

__all__ = [
    "BANGUMI_CONNECTION_FORM",
    "BangumiCredentialVerifier",
    "BANGUMI_MANIFEST",
    "BangumiClient",
    "BangumiProvider",
    "HttpxBangumiTransport",
    "BangumiTransport",
]
