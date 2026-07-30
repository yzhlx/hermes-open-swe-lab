"""Configuration loading for the Open SWE relay adapter.

All values are read from environment variables. Nothing is hard-coded.
"""

import os
from dataclasses import dataclass
from typing import Optional

ENV_BASE_URL = "OPEN_SWE_OPENAI_BASE_URL"
ENV_API_KEY = "OPEN_SWE_OPENAI_API_KEY"
ENV_MODEL = "OPEN_SWE_OPENAI_MODEL"
ENV_USE_RESPONSES = "OPEN_SWE_OPENAI_USE_RESPONSES"
ENV_DISABLE_FALLBACK = "OPEN_SWE_DISABLE_CROSS_PROVIDER_FALLBACK"
ENV_LANGSMITH_GATEWAY = "LANGSMITH_GATEWAY_ENABLED"


class RelayError(Exception):
    """Base class for all structured relay errors (carries a ``code``)."""

    code = "RELAY_ERROR"


class RelayConfigError(RelayError):
    """Explicit, structured error raised by the relay adapter.

    Raised instead of silently falling back or routing.
    """

    code = "RELAY_CONFIG_ERROR"


class RelayRequestError(RelayError):
    """Structured error for transport / runtime relay failures."""

    code = "RELAY_REQUEST_ERROR"


class RelayTimeoutError(RelayRequestError):
    """A relay request timed out (after retries if configured)."""

    code = "RELAY_TIMEOUT"


class RelayRetryExhaustedError(RelayRequestError):
    """Retries were exhausted on a retryable error (429 / 5xx / timeout)."""

    code = "RELAY_RETRY_EXHAUSTED"


class RelayInvalidResponseError(RelayRequestError):
    """The relay returned a structurally invalid / unusable response."""

    code = "RELAY_INVALID_RESPONSE"


@dataclass(frozen=True)
class RelayConfig:
    base_url: str
    api_key: str
    model: str
    use_responses: bool = False
    disable_cross_provider_fallback: bool = False

    @property
    def is_responses_api(self) -> bool:
        return self.use_responses


def _truthy(value: str) -> bool:
    return value.strip().lower() == "true"


def load_relay_config(environ=None) -> Optional[RelayConfig]:
    """Load relay configuration from the environment.

    Returns ``None`` when ``OPEN_SWE_OPENAI_BASE_URL`` is absent — this is the
    opt-in signal to preserve upstream Open SWE default behavior.

    Raises :class:`RelayConfigError` for invalid combinations, including any
    attempt to silently route through the LangSmith Gateway.
    """
    environ = os.environ if environ is None else environ

    base_url = (environ.get(ENV_BASE_URL) or "").strip()
    if not base_url:
        # Opt-in: absent relay config => preserve upstream default.
        return None

    # The relay adapter only speaks OpenAI-compatible Chat Completions over
    # http(s). The OpenAI Responses-over-WebSocket path is disabled by policy
    # (see AGENTS.md Section 10 / MVP-0). Reject ws:// or wss:// relay URLs so
    # the hardcoded upstream ``wss://api.openai.com/v1`` can never be reached
    # through this adapter.
    if base_url.startswith(("ws://", "wss://")):
        raise RelayConfigError(
            "OPEN_SWE_OPENAI_BASE_URL must use http(s); WebSocket (ws://) relay "
            "endpoints are not supported. The relay adapter uses Chat Completions "
            "over https and never enables the Responses-over-WebSocket path."
        )

    api_key = (environ.get(ENV_API_KEY) or "").strip()
    model = (environ.get(ENV_MODEL) or "").strip()
    use_responses = _truthy(environ.get(ENV_USE_RESPONSES, ""))
    disable_fallback = _truthy(environ.get(ENV_DISABLE_FALLBACK, ""))
    langsmith_gateway = (environ.get(ENV_LANGSMITH_GATEWAY) or "").strip().lower()

    # Do NOT silently route through the LangSmith Gateway (AGENTS.md Section 10).
    if langsmith_gateway == "true":
        raise RelayConfigError(
            "LANGSMITH_GATEWAY_ENABLED=true is not allowed: the relay adapter must "
            "not silently route through the LangSmith Gateway. Set it to 'false' or "
            "remove it, and provide an explicit OPEN_SWE_OPENAI_BASE_URL instead."
        )

    if not api_key:
        raise RelayConfigError(
            "OPEN_SWE_OPENAI_API_KEY is required when OPEN_SWE_OPENAI_BASE_URL is set."
        )
    if not model:
        raise RelayConfigError(
            "OPEN_SWE_OPENAI_MODEL is required when OPEN_SWE_OPENAI_BASE_URL is set."
        )

    return RelayConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        use_responses=use_responses,
        disable_cross_provider_fallback=disable_fallback,
    )
