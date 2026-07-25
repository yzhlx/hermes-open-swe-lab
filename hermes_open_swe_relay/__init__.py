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

from .client import RelayClient
from .config import RelayConfig, RelayConfigError, load_relay_config
from .redact import redact, redact_headers

__all__ = [
    "RelayConfig",
    "RelayConfigError",
    "load_relay_config",
    "RelayClient",
    "redact",
    "redact_headers",
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
