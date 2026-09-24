"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Any | None:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    on_model: Callable[[T], None] | None = None,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.

    ``on_model``, when given, is invoked with the parsed model instance right
    after a successful structured call — and never on the free-text fallback —
    so callers can capture typed output while still receiving markdown.
    """
    if structured_llm is not None:
        for attempt in (1, 2):
            try:
                result = structured_llm.invoke(prompt)
                if result is None:
                    # A thinking model can answer in plain text instead of calling
                    # the tool, leaving the parser with nothing to return. Treat it
                    # as a structured miss and fall back, with a clear reason.
                    raise ValueError("structured output returned no parsed result")
                if on_model is not None:
                    on_model(result)
                return render(result)
            except Exception as exc:
                if attempt == 1:
                    # Weak providers that bind the schema without grammar
                    # enforcement sometimes answer in prose (parser returns
                    # None) or emit one malformed payload — a plain retry
                    # usually parses, and a fallback loses typed output
                    # (e.g. standing rules captured via ``on_model``).
                    logger.warning(
                        "%s: structured-output invocation failed (%s); "
                        "retrying the structured call once",
                        agent_name, exc,
                    )
                else:
                    logger.warning(
                        "%s: structured-output invocation failed (%s); "
                        "retrying once as free text",
                        agent_name, exc,
                    )

    response = plain_llm.invoke(prompt)
    return response.content
