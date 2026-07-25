#!/usr/bin/env python3
"""Provider compatibility preflight for the Open SWE relay (checks P1-P6).

This is the verification harness that proves the relay supports the multi-turn
tool-calling behavior Open SWE requires. It is NOT the production adapter
(see ``hermes_open_swe_relay``); it talks directly to the configured relay.

Checks
------
P1  basic response
P2  single tool call                (FAIL -> PROVIDER_INCOMPATIBLE)
P3  consecutive tool calls          (FAIL -> PROVIDER_INCOMPATIBLE)
P4  streaming
P5  long context
P6  structured error handling + secret redaction

Exit codes
----------
0  all checks passed
1  PROVIDER_INCOMPATIBLE (P2 or P3 failed)
2  not configured (no relay env -> NOT_TESTED, not a failure)
3  harness/setup error (e.g. openai SDK missing)

The script NEVER prints API keys or Authorization headers; all error text and
snippets pass through ``redact()``.

Usage
-----
    python scripts/provider_preflight.py            # uses environment
    python scripts/provider_preflight.py --json     # machine-readable
    python scripts/provider_preflight.py --out FILE # also write JSON to FILE
"""

import argparse
import json
import os
import sys

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_open_swe_relay import load_relay_config, RelayConfigError, redact  # noqa: E402


def _build_openai_client(base_url: str, api_key: str):
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "The 'openai' SDK is required for live preflight. "
            "Install it with: pip install 'hermes-open-swe-relay[relay]'"
        ) from exc
    return OpenAI(base_url=base_url, api_key=api_key)


def check_p1_basic(client, model):
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        max_tokens=8,
    )
    content = (resp.choices[0].message.content or "").strip()
    return len(content) > 0, {"model": model, "preview": redact(content[:60])}


def check_p2_single_tool(client, model):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "What is the weather in Wuxi?"}],
        tools=tools,
        tool_choice="auto",
        max_tokens=80,
    )
    msg = resp.choices[0].message
    has_tool = bool(getattr(msg, "tool_calls", None))
    return has_tool, {
        "model": model,
        "tool_calls": len(getattr(msg, "tool_calls", []) or []),
    }


def check_p3_consecutive_tools(client, model):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    messages = [
        {"role": "user", "content": "What is the weather in Wuxi? Then tell me the time."}
    ]
    r1 = client.chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="auto", max_tokens=120
    )
    m1 = r1.choices[0].message
    calls_r1 = len(getattr(m1, "tool_calls", []) or [])
    # Second turn: supply a fake tool result and ask the model to continue.
    messages.append(m1)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": (m1.tool_calls[0].id if calls_r1 else "n/a"),
            "content": "Wuxi: 22C, cloudy.",
        }
    )
    messages.append({"role": "user", "content": "Thanks. Now what is the time?"})
    r2 = client.chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="auto", max_tokens=120
    )
    m2 = r2.choices[0].message
    calls_r2 = len(getattr(m2, "tool_calls", []) or [])
    # Pass if a tool was called in round 1, and round 2 produced a coherent
    # response (a tool call OR textual answer) without error.
    passed = calls_r1 >= 1 and (calls_r2 >= 1 or bool(m2.content))
    return passed, {"round1_tool_calls": calls_r1, "round2_tool_calls": calls_r2}


def check_p4_streaming(client, model):
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Count to three, one per line."}],
        stream=True,
        max_tokens=24,
    )
    chunks = []
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            chunks.append(delta)
    content = "".join(chunks)
    return len(content) > 0, {"chars": len(content)}


def check_p5_long_context(client, model):
    # ~6k tokens of repeated, harmless text to probe context handling.
    filler = ("The quick brown fox jumps over the lazy dog. ") * 1400
    prompt = (
        "Summarize the following text in exactly one sentence. Text:\n"
        + filler
        + "\nSummary:"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=40,
    )
    content = (resp.choices[0].message.content or "").strip()
    return len(content) > 0, {"prompt_chars": len(prompt)}


def check_p6_error_and_redaction(client, model):
    # Force a structured error via an invalid model name.
    raised = False
    err_text = ""
    try:
        client.chat.completions.create(
            model="nonexistent-model-xyz-0000",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=4,
        )
    except Exception as exc:  # noqa: BLE001 - we want to capture any provider error
        raised = True
        err_text = redact(str(exc))
    # Secret redaction sanity check.
    sample = redact("Authorization: Bearer sk-fake-secret-1234567890")
    redaction_ok = "sk-fake-secret" not in sample and "***REDACTED***" in sample
    passed = raised and redaction_ok
    return passed, {
        "error_captured": raised,
        "error_preview": err_text[:120],
        "redaction_ok": redaction_ok,
    }


def run_preflight(environ, client_factory=None):
    config = load_relay_config(environ)
    if config is None:
        return None  # not configured -> NOT_TESTED
    client = (client_factory or _build_openai_client)(
        config.base_url, config.api_key
    )
    checks = [
        ("P1", "basic response", lambda: check_p1_basic(client, config.model)),
        ("P2", "single tool call", lambda: check_p2_single_tool(client, config.model)),
        ("P3", "consecutive tool calls", lambda: check_p3_consecutive_tools(client, config.model)),
        ("P4", "streaming", lambda: check_p4_streaming(client, config.model)),
        ("P5", "long context", lambda: check_p5_long_context(client, config.model)),
        ("P6", "error handling + redaction", lambda: check_p6_error_and_redaction(client, config.model)),
    ]
    results = []
    for cid, name, fn in checks:
        try:
            passed, detail = fn()
        except Exception as exc:  # noqa: BLE001
            passed, detail = False, {"error": redact(str(exc))[:200]}
        results.append(
            {"id": cid, "name": name, "passed": bool(passed), "detail": detail}
        )
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open SWE relay provider preflight (P1-P6)")
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    parser.add_argument("--out", help="write JSON results to this file")
    args = parser.parse_args(argv)

    results = run_preflight(os.environ)
    if results is None:
        msg = (
            "PROVIDER_PREFLIGHT: NOT_TESTED — no relay configured "
            "(OPEN_SWE_OPENAI_BASE_URL is empty). Live checks P1-P6 require "
            "relay credentials provided at the authorization gate (Step 9)."
        )
        if args.json:
            print(json.dumps({"status": "NOT_TESTED", "reason": "no relay configured"}, indent=2))
        else:
            print(msg)
        return 2

    p2 = next((r for r in results if r["id"] == "P2"), None)
    p3 = next((r for r in results if r["id"] == "P3"), None)
    all_passed = all(r["passed"] for r in results)
    incompatible = (p2 and not p2["passed"]) or (p3 and not p3["passed"])

    summary = {
        "status": "PASS" if all_passed and not incompatible else "FAIL",
        "all_passed": all_passed,
        "provider_incompatible": bool(incompatible),
        "checks": results,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("=== Provider Preflight (P1-P6) ===")
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  [{mark}] {r['id']} {r['name']}: {r['detail']}")
        print(f"Result: {summary['status']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)

    if incompatible:
        print("STATUS: PROVIDER_INCOMPATIBLE")
        return 1
    return 0 if all_passed else 3


if __name__ == "__main__":
    sys.exit(main())
