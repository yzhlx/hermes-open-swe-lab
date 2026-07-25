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


class RelayConfigError(Exception):
    """Explicit, structured error raised by the relay adapter.

    Raised instead of silently falling back or routing.
    """

    code = "RELAY_CONFIG_ERROR"


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
