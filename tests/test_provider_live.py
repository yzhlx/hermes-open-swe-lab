"""Provider-live offline validation: P1-P6 + the 9 required behaviors.

All tests are deterministic and require NO network and NO `openai` SDK. The
relay endpoint is simulated by :class:`FakeRelay` so we can assert request
shaping, retry/backoff, streaming, tool calls, finish_reason, usage, invalid
response handling, and secret redaction.

P1  endpoint & auth
P2  specified model callable
P3  minimal non-streaming chat
P4  streaming incremental output
P5  tool-call structure compatible
P6  fixed Open SWE agent minimal no-repo task (integration-contract level;
    the live run against the real relay requires credentials + the control
    plane translation and is a user action — see PROVIDER-LIVE-RESULT.md)
"""

import json
import types

import pytest

from hermes_open_swe_relay import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    RelayClient,
    RelayConfig,
    RelayConfigError,
    RelayError,
    RelayInvalidResponseError,
    RelayRetryExhaustedError,
    StreamAccumulator,
    accumulate_chat_stream,
    build_relay_client,
    load_relay_config,
)
from hermes_open_swe_relay.client import _attr


# --------------------------------------------------------------------------- #
# Fake relay (OpenAI-compatible endpoint simulator)
# --------------------------------------------------------------------------- #
class _RelayStatusError(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__(f"status {status}")


class _RelayTimeout(Exception):
    type = "timeout"


class FakeRelay:
    """Scriptable OpenAI-compatible relay.

    ``fail_times``/``status`` drive 429/5xx/timeout retries; ``invalid`` makes a
    chat call return an unusable response; ``tools``/``stream`` shaped responses
    are produced automatically from the request.
    """

    def __init__(
        self,
        *,
        fail_times=0,
        status=429,
        retry_after=None,
        invalid=False,
        timeout=False,
    ):
        self.fail_times = fail_times
        self.status = status
        self.retry_after = retry_after
        self.invalid = invalid
        self.timeout = timeout
        self.calls = []
        self._attempt = 0

    # The fake exposes the same surface the OpenAI SDK client has.
    @property
    def chat(self):
        return types.SimpleNamespace(completions=types.SimpleNamespace(create=self._chat_create))

    @property
    def responses(self):
        return types.SimpleNamespace(create=self._resp_create)

    def _maybe_fail(self):
        self._attempt += 1
        if self._attempt <= self.fail_times:
            if self.timeout:
                raise _RelayTimeout("timed out")
            err = _RelayStatusError(self.status)
            if self.retry_after is not None:
                err.response = types.SimpleNamespace(
                    headers={"retry-after": str(self.retry_after)}
                )
            raise err
        return None

    def _chat_create(self, **kw):
        self.calls.append(("chat", dict(kw)))
        if self.invalid:
            return {}  # neither 'choices' nor 'id' -> invalid
        err = self._maybe_fail()
        if err:
            raise err
        if kw.get("stream"):
            return self._stream_chunks(kw)
        return self._chat_response(kw)

    def _resp_create(self, **kw):
        self.calls.append(("responses", dict(kw)))
        return {"id": "fake-responses", "model": kw.get("model")}

    def _chat_response(self, kw):
        messages = kw.get("messages", [])
        has_tool_role = any(m.get("role") == "tool" for m in messages)
        if has_tool_role:
            content = "2 + 2 = 4."
        elif kw.get("tools"):
            content = None
        else:
            content = "Hello from relay."
        msg = {"role": "assistant", "content": content}
        if kw.get("tools") and not has_tool_role:
            tool_name = kw["tools"][0]["function"]["name"]
            if tool_name == "get_weather":
                args_json = '{"city":"Wuxi"}'
            else:
                args_json = '{"a":2,"b":2}'
            msg["tool_calls"] = [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": args_json},
                }
            ]
        emitted_tool_call = bool(msg.get("tool_calls"))
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return {
            "id": "chatcmpl-1",
            "model": kw.get("model"),
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if emitted_tool_call else "stop"}],
            "usage": usage,
        }

    def _stream_chunks(self, kw):
        if kw.get("tools"):
            return iter(
                [
                    {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "get_", "arguments": ""}}]}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"name": "weather", "arguments": '{"city":"Wuxi"}'}}]}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                ]
            )
        return iter(
            [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [{"index": 0, "delta": {}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
            ]
        )


def fake_factory(base_url, api_key, **kwargs):
    fr = FakeRelay(**kwargs.pop("_fake_opts", {})) if "_fake_opts" in kwargs else FakeRelay()
    fr._captured = {"base_url": base_url, "api_key": api_key, "timeout": kwargs.get("timeout")}
    return fr


def _client(use_responses=False, disable_fallback=False, **client_opts):
    cfg = RelayConfig(
        base_url="https://relay.example/v1",
        api_key="sk-relay-secret",
        model="relay-model",
        use_responses=use_responses,
        disable_cross_provider_fallback=disable_fallback,
    )
    return RelayClient(cfg, openai_factory=fake_factory, **client_opts)


# --------------------------------------------------------------------------- #
# P1 — endpoint & auth
# --------------------------------------------------------------------------- #
def test_p1_opt_in_absent_returns_none():
    assert build_relay_client({}) is None


def test_p1_wss_base_url_rejected():
    with pytest.raises(RelayConfigError):
        load_relay_config(
            {
                "OPEN_SWE_OPENAI_BASE_URL": "wss://evil.example/v1",
                "OPEN_SWE_OPENAI_API_KEY": "sk-x",
                "OPEN_SWE_OPENAI_MODEL": "m",
            }
        )


def test_p1_base_url_must_be_https():
    cfg = load_relay_config(
        {
            "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
            "OPEN_SWE_OPENAI_API_KEY": "sk-x",
            "OPEN_SWE_OPENAI_MODEL": "m",
        }
    )
    assert cfg.base_url.startswith("http")  # never ws:// or wss://


def test_p1_auth_key_passed_to_client():
    client = _client()
    raw = client._get_raw()
    assert raw._captured["api_key"] == "sk-relay-secret"
    assert raw._captured["base_url"] == "https://relay.example/v1"


def test_p1_langsmith_gateway_true_rejected():
    with pytest.raises(RelayConfigError):
        load_relay_config(
            {
                "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
                "OPEN_SWE_OPENAI_API_KEY": "sk-x",
                "OPEN_SWE_OPENAI_MODEL": "m",
                "LANGSMITH_GATEWAY_ENABLED": "true",
            }
        )


# --------------------------------------------------------------------------- #
# P2 — specified model callable
# --------------------------------------------------------------------------- #
def test_p2_model_forwarded_and_echoed():
    client = _client()
    out = client.complete(messages=[{"role": "user", "content": "hi"}])
    kind, params = client._get_raw().calls[0]
    assert kind == "chat"
    assert params["model"] == "relay-model"
    assert out["model"] == "relay-model"


# --------------------------------------------------------------------------- #
# P3 — minimal non-streaming chat (content + finish_reason + usage)
# --------------------------------------------------------------------------- #
def test_p3_nonstream_content_finish_reason_usage():
    client = _client()
    out = client.complete(messages=[{"role": "user", "content": "hi"}])
    assert RelayClient.extract_content(out) == "Hello from relay."
    assert RelayClient.extract_finish_reason(out) == "stop"
    usage = RelayClient.extract_usage(out)
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


# --------------------------------------------------------------------------- #
# P4 — streaming incremental output
# --------------------------------------------------------------------------- #
def test_p4_stream_yields_incremental_deltas():
    client = _client()
    chunks = list(client.complete(messages=[{"role": "user", "content": "hi"}], stream=True))
    # 3 text deltas + 1 usage-only delta
    assert len(chunks) == 4
    joined = "".join(
        _attr(c, "choices")[0]["delta"].get("content") or "" for c in chunks
    )
    assert joined == "Hello"


def test_p4_accumulator_reconstructs_message():
    client = _client()
    stream = client.complete(messages=[{"role": "user", "content": "hi"}], stream=True)
    result = accumulate_chat_stream(stream)
    assert result["content"] == "Hello"
    assert result["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 15


# --------------------------------------------------------------------------- #
# P5 — tool-call structure compatible (non-stream + stream)
# --------------------------------------------------------------------------- #
def test_p5_nonstream_tool_calls_structure():
    client = _client()
    out = client.complete(
        messages=[{"role": "user", "content": "weather?"}],
        tools=[{"type": "function", "function": {"name": "get_weather"}}],
        tool_choice="auto",
    )
    tcs = RelayClient.extract_tool_calls(out)
    assert len(tcs) == 1
    tc = tcs[0]
    assert tc["id"] == "call_abc"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Wuxi"}
    assert RelayClient.extract_finish_reason(out) == "tool_calls"


def test_p5_stream_tool_calls_accumulate():
    client = _client()
    stream = client.complete(
        messages=[{"role": "user", "content": "weather?"}],
        tools=[{"type": "function", "function": {"name": "get_weather"}}],
        stream=True,
    )
    result = accumulate_chat_stream(stream)
    assert result["finish_reason"] == "tool_calls"
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Wuxi"}


# --------------------------------------------------------------------------- #
# P6 — fixed Open SWE agent minimal no-repo task (integration-contract)
# --------------------------------------------------------------------------- #
def test_p6_minimal_agent_no_repo_tool_loop():
    """Simulate Open SWE's Chat Completions tool loop (use_responses_api=False)
    against the relay. No GitHub repo is cloned, no PR is created, no Docker is
    used, and no protected system is touched."""
    client = _client()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Add two numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "What is 2 + 2? Use the calculator."}]

    # Turn 1: model emits a tool call.
    resp1 = client.complete(messages=messages, tools=tools, tool_choice="auto")
    tcs = RelayClient.extract_tool_calls(resp1)
    assert tcs and tcs[0]["function"]["name"] == "calculator"
    assert RelayClient.extract_finish_reason(resp1) == "tool_calls"

    # Execute the tool locally (no repo, no network).
    args = json.loads(tcs[0]["function"]["arguments"])
    result = args["a"] + args["b"]

    # Feed the tool result back, Open SWE-style.
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tcs[0]["id"],
                    "type": "function",
                    "function": {
                        "name": tcs[0]["function"]["name"],
                        "arguments": tcs[0]["function"]["arguments"],
                    },
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": tcs[0]["id"], "content": str(result)})

    # Turn 2: model gives the final answer.
    resp2 = client.complete(messages=messages, tools=tools)
    content = RelayClient.extract_content(resp2)
    assert "4" in content
    assert RelayClient.extract_finish_reason(resp2) == "stop"
    assert RelayClient.extract_usage(resp2) is not None


# --------------------------------------------------------------------------- #
# Behavior: timeout, 429/5xx retry, invalid response, redaction, fallback
# --------------------------------------------------------------------------- #
def test_timeout_default_and_percall_forwarded():
    client = _client()
    assert client.timeout == DEFAULT_TIMEOUT
    client.complete(messages=[{"role": "user", "content": "hi"}], timeout=7.5)
    _, params = client._get_raw().calls[0]
    assert params["timeout"] == 7.5


def test_429_retry_then_success_honors_retry_after():
    delays = []
    client = _client(max_retries=3, backoff_base=DEFAULT_BACKOFF_BASE, sleep=delays.append)
    # Inject scripted failures into the underlying fake.
    client._get_raw().__dict__  # ensure raw built
    raw = client._get_raw()
    raw.__class__ = FakeRelay
    raw.fail_times = 2
    raw.status = 429
    raw.retry_after = 2
    raw._attempt = 0
    out = client.complete(messages=[{"role": "user", "content": "hi"}])
    assert RelayClient.extract_content(out) == "Hello from relay."
    # 2 failures -> 2 retries; Retry-After=2 must be honoured exactly.
    assert delays == [2.0, 2.0]
    assert len(raw.calls) == 3  # 2 fails + 1 success


def test_5xx_retry_then_success():
    client = _client(max_retries=3, sleep=lambda *_: None)
    raw = client._get_raw()
    raw.fail_times = 1
    raw.status = 503
    raw.retry_after = None
    raw._attempt = 0
    out = client.complete(messages=[{"role": "user", "content": "hi"}])
    assert RelayClient.extract_content(out) == "Hello from relay."
    assert len(raw.calls) == 2


def test_retry_exhausted_raises():
    client = _client(max_retries=3, sleep=lambda *_: None)
    raw = client._get_raw()
    raw.fail_times = 99
    raw.status = 500
    raw.retry_after = None
    raw._attempt = 0
    with pytest.raises(RelayRetryExhaustedError):
        client.complete(messages=[{"role": "user", "content": "hi"}])


def test_non_retryable_error_not_retried():
    client = _client(max_retries=3, sleep=lambda *_: None)
    raw = client._get_raw()
    raw.fail_times = 99
    raw.status = 400  # 400 is not retryable
    raw.retry_after = None
    raw._attempt = 0
    with pytest.raises(_RelayStatusError):  # original type preserved
        client.complete(messages=[{"role": "user", "content": "hi"}])
    assert len(raw.calls) == 1  # not retried


def test_timeout_retried_then_success_and_exhausted():
    # success after one timeout
    client = _client(max_retries=3, sleep=lambda *_: None)
    raw = client._get_raw()
    raw.fail_times = 1
    raw.timeout = True
    raw._attempt = 0
    out = client.complete(messages=[{"role": "user", "content": "hi"}])
    assert RelayClient.extract_content(out) == "Hello from relay."
    # exhausted on persistent timeouts
    client2 = _client(max_retries=2, sleep=lambda *_: None)
    raw2 = client2._get_raw()
    raw2.fail_times = 99
    raw2.timeout = True
    raw2._attempt = 0
    with pytest.raises(RelayRetryExhaustedError):
        client2.complete(messages=[{"role": "user", "content": "hi"}])


def test_invalid_response_raises():
    client = _client()
    raw = client._get_raw()
    raw.invalid = True
    with pytest.raises(RelayInvalidResponseError):
        client.complete(messages=[{"role": "user", "content": "hi"}])


def test_invalid_response_none_raises():
    with pytest.raises(RelayInvalidResponseError):
        RelayClient.validate_response(None)


def test_secret_redaction_strips_key_in_error_text():
    text = RelayClient.redact("request failed for key sk-relay-secret-12345")
    assert "sk-relay-secret-12345" not in text
    assert "***REDACTED***" in text


def test_cross_provider_fallback_definitively_closed():
    client = _client(disable_fallback=True)
    assert client.cross_provider_fallback_enabled is False
    with pytest.raises(RelayConfigError):
        client.enable_cross_provider_fallback("anthropic")
    # The adapter never configures an Anthropic fallback provider.
    assert client._cross_provider_fallback is False
    # Config flag is reflected.
    assert client.config.disable_cross_provider_fallback is True


def test_chat_completions_is_default_path():
    client = _client(use_responses=False)
    out = client.complete(messages=[{"role": "user", "content": "hi"}])
    kind, _ = client._get_raw().calls[0]
    assert kind == "chat"  # not 'responses'
