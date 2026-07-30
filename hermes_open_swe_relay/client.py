"""Relay client wrapper for Open SWE.

This is the integration/verification layer the control plane uses to talk to an
OpenAI-compatible relay. It is intentionally thin but production-grade:

* defaults to Chat Completions unless ``OPEN_SWE_OPENAI_USE_RESPONSES=true``;
* never uses the OpenAI Responses-over-WebSocket path (the relay base URL must
  be http(s); ``wss://`` is rejected at config load — see :mod:`config`);
* preserves tool calling by forwarding ``tools`` / ``tool_choice`` unchanged;
* applies a timeout and explicit 429 / 5xx / timeout retry-with-backoff
  (Retry-After aware, owned entirely by this adapter — the SDK ``max_retries``
  is forced to 0 so there is a single, observable retry policy);
* validates responses and raises structured :class:`RelayRequestError` variants;
* never logs or propagates API keys / ``Authorization`` headers (see :mod:`redact`).

Nothing is hard-coded: the URL, key and model come only from the environment
loaded by :mod:`config`. Cross-provider (Anthropic) fallback is always off.
"""

import logging
import time
from typing import Any, Callable, Iterator, Optional

from .config import (
    RelayConfig,
    RelayConfigError,
    RelayError,
    RelayInvalidResponseError,
    RelayRetryExhaustedError,
)
from .redact import redact

logger = logging.getLogger("hermes_open_swe_relay")

# Code-level defaults (overridable per client / per call). These are NOT new
# environment variables — they keep the adapter self-contained and avoid adding
# config names outside the approved set in AGENTS.md Section 10.
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0


def _default_openai_factory(base_url: str, api_key: str, **kwargs):
    # Lazy import: the `openai` SDK is only required when actually calling the
    # relay, never at import time or during offline unit tests.
    from openai import OpenAI

    # Force the SDK's own retry off; this adapter owns the retry/backoff policy.
    return OpenAI(base_url=base_url, api_key=api_key, max_retries=0, **kwargs)


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from either an attribute or a dict item (OpenAI SDK objects
    behave like both depending on version)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class RelayClient:
    def __init__(
        self,
        config: RelayConfig,
        openai_factory: Optional[Callable[..., Any]] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._factory = openai_factory or _default_openai_factory
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff_base = float(backoff_base)
        self._sleep = sleep
        self._raw = None
        # The adapter never enables a cross-provider (Anthropic) fallback.
        self._cross_provider_fallback = False

    # ------------------------------------------------------------------ #
    # Cross-provider fallback policy (always disabled)
    # ------------------------------------------------------------------ #
    @property
    def cross_provider_fallback_enabled(self) -> bool:
        return self._cross_provider_fallback

    def enable_cross_provider_fallback(self, provider: str = "anthropic") -> None:
        # Explicitly forbidden: the adapter must not silently fall back.
        raise RelayConfigError(
            f"Cross-provider fallback to '{provider}' is disabled by policy. "
            "Open SWE must use the configured OpenAI-compatible relay only."
        )

    # ------------------------------------------------------------------ #
    # Low-level client
    # ------------------------------------------------------------------ #
    def _get_raw(self):
        if self._raw is None:
            self._raw = self._factory(
                base_url=self.config.base_url,
                api_key=self.config.api_key,
                timeout=self.timeout,
            )
        return self._raw

    # ------------------------------------------------------------------ #
    # Retry / backoff
    # ------------------------------------------------------------------ #
    @staticmethod
    def _status_code(exc: BaseException) -> Optional[int]:
        code = getattr(exc, "status_code", None)
        return code if isinstance(code, int) else None

    @classmethod
    def _is_retryable(cls, exc: BaseException) -> bool:
        code = cls._status_code(exc)
        if code == 429:
            return True
        if code is not None and 500 <= code < 600:
            return True
        # Timeouts (openai.APITimeoutError exposes .type == "timeout").
        if isinstance(exc, TimeoutError):
            return True
        if getattr(exc, "type", None) == "timeout":
            return True
        if "Timeout" in type(exc).__name__:
            return True
        return False

    @classmethod
    def _retry_after_seconds(cls, exc: BaseException, attempt: int, base: float) -> float:
        # Honour Retry-After when present (httpx/openai expose .response.headers).
        resp = getattr(exc, "response", None)
        headers = getattr(resp, "headers", None)
        raw = None
        if isinstance(headers, dict):
            raw = headers.get("retry-after")
        elif headers is not None and hasattr(headers, "get"):
            try:
                raw = headers.get("retry-after")
            except Exception:  # noqa: BLE001
                raw = None
        else:
            raw = getattr(exc, "retry_after", None)
        if raw is not None:
            try:
                val = float(raw)
                if val >= 0:
                    return val
            except (TypeError, ValueError):
                pass
        # Exponential backoff, capped, to avoid thundering herds.
        return min(base * (2 ** attempt), 30.0)

    def _with_retry(self, fn: Callable[[], Any]) -> Any:
        attempt = 0
        while True:
            try:
                return fn()
            except RelayError:
                # Structured relay errors are never retried.
                raise
            except Exception as exc:  # noqa: BLE001
                if attempt >= self.max_retries or not self._is_retryable(exc):
                    if self._is_retryable(exc):
                        raise RelayRetryExhaustedError(
                            redact(
                                f"Relay request failed after {self.max_retries} retries: "
                                f"{type(exc).__name__}"
                            )
                        ) from exc
                    # Non-retryable: propagate the original error unchanged.
                    raise
                delay = self._retry_after_seconds(exc, attempt, self.backoff_base)
                logger.debug(
                    "relay retry %s/%.0f in %.2fs due to %s",
                    attempt + 1,
                    self.max_retries,
                    delay,
                    type(exc).__name__,
                )
                self._sleep(delay)
                attempt += 1

    # ------------------------------------------------------------------ #
    # Response validation (invalid-response handling)
    # ------------------------------------------------------------------ #
    @staticmethod
    def validate_response(resp: Any) -> None:
        """Raise :class:`RelayInvalidResponseError` for unusable responses.

        A response with ``choices`` must have at least one choice. A response
        with neither ``choices`` nor an ``id`` is considered structurally
        invalid. Stub shapes that carry only an ``id`` are tolerated (they
        cannot be validated without the full schema).
        """
        if resp is None:
            raise RelayInvalidResponseError("Relay returned None instead of a response.")
        if isinstance(resp, dict):
            has_choices = "choices" in resp
            has_id = "id" in resp
        else:
            has_choices = hasattr(resp, "choices")
            has_id = hasattr(resp, "id")
        if has_choices:
            choices = _attr(resp, "choices")
            if not choices:
                raise RelayInvalidResponseError("Relay returned an empty 'choices' list.")
            return
        if not has_id:
            raise RelayInvalidResponseError(
                "Relay returned a response with neither 'choices' nor 'id'."
            )

    # ------------------------------------------------------------------ #
    # Public call paths
    # ------------------------------------------------------------------ #
    def chat_completion(self, **params: Any) -> Any:
        """Chat Completions path (default). Preserves tool calling."""
        params = dict(params)
        params.setdefault("model", self.config.model)
        has_tools = bool(params.get("tools"))
        logger.debug(
            "relay chat.completions.create model=%s tools=%s stream=%s",
            self.config.model,
            has_tools,
            bool(params.get("stream")),
        )
        result = self._with_retry(
            lambda: self._get_raw().chat.completions.create(**params)
        )
        if not params.get("stream"):
            self.validate_response(result)
        return result

    def chat_completion_stream(self, **params: Any) -> Iterator[Any]:
        """Streaming Chat Completions. Returns the chunk iterator as-is."""
        params = dict(params)
        params.setdefault("model", self.config.model)
        params["stream"] = True
        logger.debug("relay chat.completions.create(stream) model=%s", self.config.model)
        return self._with_retry(
            lambda: self._get_raw().chat.completions.create(**params)
        )

    def responses_create(self, **params: Any) -> Any:
        if not self.config.is_responses_api:
            raise RelayConfigError(
                "Responses API requested but OPEN_SWE_OPENAI_USE_RESPONSES != 'true'. "
                "Set OPEN_SWE_OPENAI_USE_RESPONSES=true or use chat_completion()."
            )
        params = dict(params)
        params.setdefault("model", self.config.model)
        logger.debug("relay responses.create model=%s", self.config.model)
        # Note: the Responses API returns 'output' items, not 'choices'; we do
        # not enforce the Chat-Completions schema here.
        return self._with_retry(lambda: self._get_raw().responses.create(**params))

    def complete(self, *, use_responses: Optional[bool] = None, **params: Any) -> Any:
        """Public entry point. Defaults to Chat Completions.

        ``use_responses`` overrides the configuration for a single call; when
        omitted, the configured ``use_responses`` flag decides. Streaming always
        uses the Chat Completions streaming path.
        """
        stream = bool(params.get("stream"))
        use_resp = (
            use_responses if use_responses is not None else self.config.is_responses_api
        )
        if stream:
            return self.chat_completion_stream(**params)
        if use_resp:
            return self.responses_create(**params)
        return self.chat_completion(**params)

    # ------------------------------------------------------------------ #
    # Response helpers (tool_calls / finish_reason / usage extraction)
    # ------------------------------------------------------------------ #
    @staticmethod
    def extract_content(resp: Any) -> Optional[str]:
        msg = _attr(_attr(resp, "choices", [{}])[0], "message")
        return _attr(msg, "content")

    @staticmethod
    def extract_finish_reason(resp: Any) -> Optional[str]:
        choice = _attr(resp, "choices", [{}])[0]
        return _attr(choice, "finish_reason")

    @staticmethod
    def extract_usage(resp: Any) -> Optional[dict]:
        usage = _attr(resp, "usage")
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage
        try:
            return {
                "prompt_tokens": _attr(usage, "prompt_tokens"),
                "completion_tokens": _attr(usage, "completion_tokens"),
                "total_tokens": _attr(usage, "total_tokens"),
            }
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def extract_tool_calls(resp: Any) -> list:
        msg = _attr(_attr(resp, "choices", [{}])[0], "message")
        tcs = _attr(msg, "tool_calls")
        if not tcs:
            return []
        out = []
        for tc in tcs:
            fn = _attr(tc, "function", {}) or {}
            out.append(
                {
                    "id": _attr(tc, "id"),
                    "type": _attr(tc, "type", "function"),
                    "function": {
                        "name": _attr(fn, "name"),
                        "arguments": _attr(fn, "arguments", ""),
                    },
                }
            )
        return out

    @staticmethod
    def redact(text: str) -> str:
        return redact(text)
