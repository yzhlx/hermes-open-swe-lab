"""Optional OpenAI-compatible relay adapter for Open SWE (MVP-0).

Opt-in: when no relay configuration is present, :func:`build_relay_client`
returns ``None`` and the caller should preserve upstream Open SWE default
behavior. When configured, the adapter routes to the relay while keeping
Chat Completions as the default path, preserving tool calling, disabling
cross-provider fallback when requested, and refusing to silently route
through the LangSmith Gateway.

No relay URL, key, or model is ever hard-coded here. All values come from
environment variables (see :mod:`hermes_open_swe_relay.config`).
"""

from .client import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    RelayClient,
)
from .config import (
    RelayConfig,
    RelayConfigError,
    RelayError,
    RelayInvalidResponseError,
    RelayRequestError,
    RelayRetryExhaustedError,
    RelayTimeoutError,
    load_relay_config,
)
from .redact import redact, redact_headers
from .streaming import StreamAccumulator, accumulate_chat_stream

__all__ = [
    "RelayConfig",
    "RelayConfigError",
    "RelayError",
    "RelayRequestError",
    "RelayTimeoutError",
    "RelayRetryExhaustedError",
    "RelayInvalidResponseError",
    "load_relay_config",
    "RelayClient",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_BACKOFF_BASE",
    "redact",
    "redact_headers",
    "StreamAccumulator",
    "accumulate_chat_stream",
    "build_relay_client",
]


def build_relay_client(environ=None):
    """Build a :class:`RelayClient` from the environment.

    Returns ``None`` when no relay base URL is configured, signalling the
    caller to preserve upstream Open SWE default behavior (opt-in adapter).
    """
    config = load_relay_config(environ)
    if config is None:
        return None
    return RelayClient(config)
