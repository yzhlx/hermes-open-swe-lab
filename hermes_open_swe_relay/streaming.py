"""Streaming accumulator for OpenAI-compatible Chat Completions streams.

Consumes an iterator of chunk objects (OpenAI SDK style or plain dicts) and
reconstructs the final message: concatenated content, accumulated tool calls,
``finish_reason`` and ``usage``. Used by the provider-live tests and by the
integration layer that drives Open SWE's Chat Completions streaming path.
"""

from typing import Any, Dict, Iterator, List, Optional


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class StreamAccumulator:
    """Incrementally ingest SSE chunks and reconstruct the full message."""

    def __init__(self) -> None:
        self.content: List[str] = []
        self.tool_calls: Dict[int, Dict[str, Any]] = {}
        self.finish_reason: Optional[str] = None
        self.usage: Optional[dict] = None

    def ingest(self, chunk: Any) -> Iterator[str]:
        """Process one chunk; yield the textual delta (if any)."""
        choices = _attr(chunk, "choices", [{}])
        if not choices:
            return
        choice = choices[0]
        delta = _attr(choice, "delta", {}) or {}
        text = _attr(delta, "content")
        if text:
            self.content.append(text)
            yield text

        for tc in _attr(delta, "tool_calls") or []:
            idx = _attr(tc, "index", 0)
            slot = self.tool_calls.setdefault(
                idx,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            tc_id = _attr(tc, "id")
            if tc_id:
                slot["id"] = tc_id
            fn = _attr(tc, "function", {}) or {}
            name = _attr(fn, "name")
            if name:
                slot["function"]["name"] += name
            arguments = _attr(fn, "arguments")
            if arguments is not None:
                slot["function"]["arguments"] += arguments

        fr = _attr(choice, "finish_reason")
        if fr:
            self.finish_reason = fr

        usage = _attr(chunk, "usage")
        if usage is not None:
            self.usage = (
                usage
                if isinstance(usage, dict)
                else {
                    "prompt_tokens": _attr(usage, "prompt_tokens"),
                    "completion_tokens": _attr(usage, "completion_tokens"),
                    "total_tokens": _attr(usage, "total_tokens"),
                }
            )

    def finalize(self) -> Dict[str, Any]:
        return {
            "content": "".join(self.content),
            "tool_calls": [self.tool_calls[i] for i in sorted(self.tool_calls)],
            "finish_reason": self.finish_reason,
            "usage": self.usage,
        }


def accumulate_chat_stream(stream: Iterator[Any]) -> Dict[str, Any]:
    """Consume a full stream and return the reconstructed message dict."""
    acc = StreamAccumulator()
    for chunk in stream:
        for _ in acc.ingest(chunk):
            pass
    return acc.finalize()
