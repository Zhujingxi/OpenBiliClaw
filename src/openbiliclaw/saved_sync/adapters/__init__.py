"""Native-save platform adapter exports."""

from ..router import NativeSaveAdapter
from .bilibili import BilibiliNativeSaveAdapter
from .extension import (
    ExtensionAdapterDefinition,
    ExtensionNativeSaveAdapter,
    build_extension_native_save_adapters,
)

__all__ = [
    "BilibiliNativeSaveAdapter",
    "ExtensionAdapterDefinition",
    "ExtensionNativeSaveAdapter",
    "NativeSaveAdapter",
    "build_extension_native_save_adapters",
]
