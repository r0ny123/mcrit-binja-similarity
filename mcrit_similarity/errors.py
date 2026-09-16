class McritError(Exception):
    """Base error for MCRIT transport and provider failures."""


class McritAuthError(McritError):
    """The server rejected the request (401/403). mcritweb persist needs contributor/admin."""


class McritHttpError(McritError):
    """Unexpected HTTP failure talking to MCRIT."""


class McritJobError(McritError):
    """A server job failed, was terminated, or was cancelled."""


class McritUnavailableError(McritError):
    """The server could not be reached or did not return a usable payload."""


class UnsupportedArchitectureError(McritError):
    """SMDA's Binary Ninja exporter only lifts intel and aarch64."""
