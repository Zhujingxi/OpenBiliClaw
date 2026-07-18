"""Error types for the OpenClaw adapter layer."""


class OpenClawAdapterError(Exception):
    """Base error for OpenClaw integration failures."""


class AdapterInitializationError(OpenClawAdapterError):
    """Raised when adapter bootstrap fails."""


class AdapterValidationError(OpenClawAdapterError):
    """Raised when adapter input validation fails."""


class AdapterOperationError(OpenClawAdapterError):
    """Raised when an adapter operation cannot be completed."""
