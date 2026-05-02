class PypeClientError(Exception):
    """Base for everything raised by pype-client."""


class PypeAuthError(PypeClientError):
    """Server returned 401 Unauthorized."""


class PypeForbiddenError(PypeClientError):
    """Server returned 403 Forbidden."""


class PypeBadRequestError(PypeClientError):
    """Server returned 400 Bad Request."""


class PypeTimeoutError(PypeClientError, TimeoutError):
    """Server returned 503 (queue full at deadline) or local deadline elapsed."""


class PypeNetworkError(PypeClientError):
    """Network-level failure: connection refused, DNS, TLS, socket timeout, etc."""


class PypeProtocolError(PypeClientError):
    """Server returned an unexpected status or malformed response we cannot map."""
