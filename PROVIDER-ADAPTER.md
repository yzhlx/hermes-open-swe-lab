# PROVIDER-ADAPTER.md — OpenAI-compatible relay adapter

**Code:** `hermes_open_swe_relay/` (package) · tests in `tests/`
**Spec:** AGENTS.md Section 10 + MVP-0 Step 6

## What it is

A thin, **opt-in** adapter that lets Open SWE talk to an OpenAI-compatible relay
API instead of the official OpenAI endpoint. When no relay is configured, the
adapter returns `None` and Open SWE keeps its upstream default behavior
unchanged.

## Configuration (all from environment; nothing hard-coded)

| Variable | Meaning | Default |
| --- | --- | --- |
| `OPEN_SWE_OPENAI_BASE_URL` | relay base URL | empty → opt-out (upstream default) |
| `OPEN_SWE_OPENAI_API_KEY` | relay key | required when base URL set |
| `OPEN_SWE_OPENAI_MODEL` | model name | required when base URL set |
| `OPEN_SWE_OPENAI_USE_RESPONSES` | use Responses API? | `false` → Chat Completions |
| `OPEN_SWE_DISABLE_CROSS_PROVIDER_FALLBACK` | forbid Anthropic fallback | `false` |
| `LANGSMITH_GATEWAY_ENABLED` | must stay `false` | `false` |

## Guarantees (and how they are enforced)

1. **Opt-in / no weakening.** `load_relay_config({})` returns `None`; the caller
   preserves upstream behavior. No relay URL/key/model is hard-coded.
2. **Chat Completions default.** `use_responses` is `False` unless
   `OPEN_SWE_OPENAI_USE_RESPONSES=true`. `complete()` routes to
   `chat.completions.create` by default.
3. **Tool calling preserved.** `tools` / `tool_choice` are forwarded unchanged to
   the relay (see `client.py:chat_completion`).
4. **No silent cross-provider fallback.** `cross_provider_fallback` is always
   `False`; `enable_cross_provider_fallback()` raises `RelayConfigError`.
5. **No silent LangSmith Gateway routing.** `LANGSMITH_GATEWAY_ENABLED=true`
   raises `RelayConfigError` at config load (never silently routes).
6. **Structured errors.** `RelayConfigError` carries `code="RELAY_CONFIG_ERROR"`.
7. **Secret redaction.** `redact()` / `redact_headers()` strip `Authorization`
   headers and API keys from any log/error text. The adapter never logs
   `Authorization` headers or keys.

## Entry points

```python
from hermes_open_swe_relay import build_relay_client, load_relay_config

client = build_relay_client()          # None when no relay configured
if client is not None:
    resp = client.complete(messages=[...], tools=[...])   # Chat Completions
```

## Test evidence

```bash
.venv/bin/python -m pytest -v
# 17 passed
```

Offline, deterministic, no network or `openai` SDK required (the low-level
client is injected as a fake in tests). See `tests/test_adapter.py` and
`tests/test_redact.py` for the full matrix: opt-in, config precedence,
Responses disabled mode, tool-call passthrough, fallback forbidden, LangSmith
Gateway refusal, and secret redaction.

## Relationship to upstream

The adapter does **not** modify `langchain-ai/open-swe` source. It is the
integration layer the control plane uses; the pinned Open SWE build consumes
these env vars at runtime in the sandbox.
