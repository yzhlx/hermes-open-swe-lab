"""Relay client wrapper for Open SWE.

The adapter is intentionally thin: it builds an OpenAI-compatible client
pointing at the configured relay ``base_url`` and exposes two call paths:

* Chat Completions (default) — used unless ``use_responses`` is enabled.
* Responses API — only when ``OPEN_SWE_OPENAI_USE_RESPONSES=true``.

Tool calling is preserved by forwarding ``tools`` / ``tool_choice`` unchanged.
Cross-provider fallback (e.g. to Anthropic) is never enabled; when
``OPEN_SWE_DISABLE_CROSS_PROVIDER_FALLBACK=true`` it is explicitly forbidden.
"""

import logging
from typing import Any, Callable, Optional

from .config import RelayConfig, RelayConfigError
from .redact import redact

logger = logging.getLogger("hermes_open_swe_relay")


def _default_openai_factory(base_url: str, api_key: str, **kwargs):
    # Lazy import: the `openai` SDK is only required when actually calling the
    # relay, never at import time or during offline unit tests.
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key, **kwargs)


class RelayClient:
    def __init__(
        self,
        config: RelayConfig,
        openai_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.config = config
        self._factory = openai_factory or _default_openai_factory
        self._raw = None
        # The adapter never enables a cross-provider (Anthropic) fallback.
        # When the user requests disabling it, we keep it disabled and make
        # any attempt to turn it on raise explicitly.
        self._cross_provider_fallback = False

    @property
    def cross_provider_fallback_enabled(self) -> bool:
        return self._cross_provider_fallback

    def enable_cross_provider_fallback(self, provider: str = "anthropic") -> None:
        # Explicitly forbidden: the adapter must not silently fall back.
        raise RelayConfigError(
            f"Cross-provider fallback to '{provider}' is disabled by policy. "
            "Open SWE must use the configured OpenAI-compatible relay only."
        )

    def _get_raw(self):
        if self._raw is None:
            self._raw = self._factory(
                base_url=self.config.base_url,
                api_key=self.config.api_key,
            )
        return self._raw

    def chat_completion(self, **params: Any) -> Any:
        """Chat Completions path (default). Preserves tool calling."""
        params = dict(params)
        params.setdefault("model", self.config.model)
        has_tools = bool(params.get("tools"))
        # Never log Authorization headers or keys.
        logger.debug(
            "relay chat.completions.create model=%s tools=%s",
            self.config.model,
            has_tools,
        )
        return self._get_raw().chat.completions.create(**params)

    def responses_create(self, **params: Any) -> Any:
        if not self.config.is_responses_api:
            raise RelayConfigError(
                "Responses API requested but OPEN_SWE_OPENAI_USE_RESPONSES != 'true'. "
                "Set OPEN_SWE_OPENAI_USE_RESPONSES=true or use chat_completion()."
            )
        params = dict(params)
        params.setdefault("model", self.config.model)
        logger.debug("relay responses.create model=%s", self.config.model)
        return self._get_raw().responses.create(**params)

    def complete(self, *, use_responses: Optional[bool] = None, **params: Any) -> Any:
        """Public entry point. Defaults to Chat Completions.

        ``use_responses`` overrides the configuration for a single call; when
        omitted, the configured ``use_responses`` flag decides.
        """
        if use_responses if use_responses is not None else self.config.is_responses_api:
            return self.responses_create(**params)
        return self.chat_completion(**params)

    @staticmethod
    def redact(text: str) -> str:
        return redact(text)
