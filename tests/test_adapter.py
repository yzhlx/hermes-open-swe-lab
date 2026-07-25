"""Offline unit tests for the relay adapter.

These tests do NOT require network access or the `openai` SDK. The low-level
client is replaced by a fake factory so we can assert routing, precedence,
tool-call preservation, and fallback behavior deterministically.
"""

import types

import pytest

from hermes_open_swe_relay import (
    RelayClient,
    RelayConfig,
    RelayConfigError,
    build_relay_client,
)
from hermes_open_swe_relay.config import load_relay_config


class FakeRaw:
    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._chat)
        )
        self.responses = types.SimpleNamespace(create=self._resp)

    def _chat(self, **kw):
        self.calls.append(("chat", kw))
        return {"id": "fake-chat", "model": kw.get("model")}

    def _resp(self, **kw):
        self.calls.append(("responses", kw))
        return {"id": "fake-responses"}


def fake_factory(base_url, api_key, **kwargs):
    return FakeRaw()


# --------------------------------------------------------------------------
# Configuration / opt-in
# --------------------------------------------------------------------------


def test_opt_in_absent_returns_none():
    assert load_relay_config({}) is None
    assert load_relay_config({"OTHER": "x"}) is None


def test_full_config_parsed():
    cfg = load_relay_config(
        {
            "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
            "OPEN_SWE_OPENAI_API_KEY": "sk-zzz",
            "OPEN_SWE_OPENAI_MODEL": "relay-model",
            "OPEN_SWE_OPENAI_USE_RESPONSES": "true",
            "OPEN_SWE_DISABLE_CROSS_PROVIDER_FALLBACK": "true",
        }
    )
    assert isinstance(cfg, RelayConfig)
    assert cfg.base_url == "https://relay.example/v1"
    assert cfg.api_key == "sk-zzz"
    assert cfg.model == "relay-model"
    assert cfg.use_responses is True
    assert cfg.disable_cross_provider_fallback is True


def test_use_responses_defaults_false():
    cfg = load_relay_config(
        {
            "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
            "OPEN_SWE_OPENAI_API_KEY": "sk-zzz",
            "OPEN_SWE_OPENAI_MODEL": "relay-model",
        }
    )
    assert cfg.use_responses is False  # Chat Completions default


def test_missing_api_key_raises():
    with pytest.raises(RelayConfigError):
        load_relay_config(
            {
                "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
                "OPEN_SWE_OPENAI_MODEL": "relay-model",
            }
        )


def test_missing_model_raises():
    with pytest.raises(RelayConfigError):
        load_relay_config(
            {
                "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
                "OPEN_SWE_OPENAI_API_KEY": "sk-zzz",
            }
        )


def test_langsmith_gateway_true_raises():
    with pytest.raises(RelayConfigError):
        load_relay_config(
            {
                "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
                "OPEN_SWE_OPENAI_API_KEY": "sk-zzz",
                "OPEN_SWE_OPENAI_MODEL": "relay-model",
                "LANGSMITH_GATEWAY_ENABLED": "true",
            }
        )


def test_langsmith_gateway_false_ok():
    cfg = load_relay_config(
        {
            "OPEN_SWE_OPENAI_BASE_URL": "https://relay.example/v1",
            "OPEN_SWE_OPENAI_API_KEY": "sk-zzz",
            "OPEN_SWE_OPENAI_MODEL": "relay-model",
            "LANGSMITH_GATEWAY_ENABLED": "false",
        }
    )
    assert cfg is not None


# --------------------------------------------------------------------------
# Client routing
# --------------------------------------------------------------------------


def _client(use_responses=False, disable_fallback=False):
    cfg = RelayConfig(
        base_url="https://relay.example/v1",
        api_key="sk-zzz",
        model="relay-model",
        use_responses=use_responses,
        disable_cross_provider_fallback=disable_fallback,
    )
    return RelayClient(cfg, openai_factory=fake_factory)


def test_build_client_returns_none_without_config():
    assert build_relay_client({}) is None


def test_chat_completion_default_path_preserves_tools():
    client = _client(use_responses=False)
    tools = [{"type": "function", "function": {"name": "run_tests"}}]
    out = client.complete(tools=tools, tool_choice="auto", messages=[])
    assert out["model"] == "relay-model"
    kind, params = client._get_raw().calls[0]
    assert kind == "chat"
    assert params["model"] == "relay-model"
    assert params["tools"] == tools
    assert params["tool_choice"] == "auto"


def test_responses_api_when_enabled():
    client = _client(use_responses=True)
    out = client.complete(use_responses=True, input="hi")
    kind, _ = client._get_raw().calls[0]
    assert kind == "responses"


def test_responses_api_raises_when_disabled():
    client = _client(use_responses=False)
    with pytest.raises(RelayConfigError):
        client.complete(use_responses=True, input="hi")


def test_cross_provider_fallback_disabled_and_forbidden():
    client = _client(disable_fallback=True)
    assert client.cross_provider_fallback_enabled is False
    with pytest.raises(RelayConfigError):
        client.enable_cross_provider_fallback("anthropic")
