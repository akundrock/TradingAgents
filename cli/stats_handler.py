import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


def _usage_to_counts(usage: Any) -> tuple[int, int] | None:
    """Map OpenAI / LangChain usage dicts to (input_tokens, output_tokens)."""
    if not usage:
        return None
    if not isinstance(usage, dict):
        return None

    tokens_in = usage.get("input_tokens")
    if tokens_in is None:
        tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("output_tokens")
    if tokens_out is None:
        tokens_out = usage.get("completion_tokens", 0)

    try:
        in_count = int(tokens_in or 0)
        out_count = int(tokens_out or 0)
    except (TypeError, ValueError):
        return None
    if in_count == 0 and out_count == 0:
        return None
    return in_count, out_count


def extract_token_usage_from_llm_result(response: LLMResult) -> tuple[int, int] | None:
    """Extract token counts from an LLMResult across LangChain usage fields."""
    llm_output = response.llm_output
    if isinstance(llm_output, dict):
        for key in ("token_usage", "usage"):
            counts = _usage_to_counts(llm_output.get(key))
            if counts:
                return counts

    try:
        generation = response.generations[0][0]
    except (IndexError, TypeError):
        return None

    if hasattr(generation, "message"):
        message = generation.message
        if isinstance(message, AIMessage):
            counts = _usage_to_counts(getattr(message, "usage_metadata", None))
            if counts:
                return counts
            response_metadata = getattr(message, "response_metadata", None)
            if isinstance(response_metadata, dict):
                for key in ("token_usage", "usage"):
                    counts = _usage_to_counts(response_metadata.get(key))
                    if counts:
                        return counts

    generation_info = getattr(generation, "generation_info", None)
    if isinstance(generation_info, dict):
        counts = _usage_to_counts(generation_info.get("usage"))
        if counts:
            return counts

    return None


class StatsCallbackHandler(BaseCallbackHandler):
    """Callback handler that tracks LLM calls, tool calls, and token usage."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when an LLM starts."""
        with self._lock:
            self.llm_calls += 1

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when a chat model starts."""
        with self._lock:
            self.llm_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Extract token usage from LLM response."""
        counts = extract_token_usage_from_llm_result(response)
        if counts:
            with self._lock:
                self.tokens_in += counts[0]
                self.tokens_out += counts[1]

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Increment tool call counter when a tool starts."""
        with self._lock:
            self.tool_calls += 1

    def get_stats(self) -> dict[str, Any]:
        """Return current statistics."""
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
