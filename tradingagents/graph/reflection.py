# TradingAgents/graph/reflection.py

from typing import Any


class Reflector:
    """Handles reflection on trading decisions."""

    def __init__(self, quick_thinking_llm: Any):
        """Initialize the reflector with an LLM."""
        self.quick_thinking_llm = quick_thinking_llm
        self.log_reflection_prompt = self._get_log_reflection_prompt()
        self.import_reflection_prompt = self._get_import_reflection_prompt()

    def _get_log_reflection_prompt(self) -> str:
        """Concise prompt for reflect_on_final_decision (Phase B log entries).

        Produces 2-4 sentences of plain prose — compact enough to be re-injected
        into future agent prompts without bloating the context window.
        """
        return (
            "You are a trading analyst reviewing your own past decision now that the outcome is known.\n"
            "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).\n\n"
            "Cover in order:\n"
            "1. Was the directional call correct? (cite the alpha figure)\n"
            "2. Which part of the investment thesis held or failed?\n"
            "3. One concrete lesson to apply to the next similar analysis.\n\n"
            "Be specific and terse. Your output will be stored verbatim in a decision log "
            "and re-read by future analysts, so every word must earn its place."
        )

    def _get_import_reflection_prompt(self) -> str:
        """Prompt for imported trade reflections with technical and fundamentals context."""
        return (
            "You are reviewing an imported historical trade from an execution journal.\n"
            "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).\n\n"
            "Cover in order:\n"
            "1. Whether the trade direction and timing were supported by the technical context at entry.\n"
            "2. Whether the observed outcome aligned with fundamentals context (or proxy fundamentals if provided).\n"
            "3. One concrete, reusable lesson for future similar setups.\n\n"
            "Be specific and concise. The output is stored as persistent memory and reused by future agent runs."
        )

    def reflect_on_final_decision(
        self,
        final_decision: str,
        raw_return: float,
        alpha_return: float,
        benchmark_name: str = "SPY",
    ) -> str:
        """Single reflection call on the final trade decision with outcome context.

        Used by Phase B deferred reflection. The final_trade_decision already
        synthesises all analyst insights, so no separate market context is needed.
        ``benchmark_name`` is the label used for the alpha line (e.g. ``"SPY"``
        for US tickers, ``"^N225"`` for ``.T`` listings); defaults to SPY for
        callers that haven't been updated to thread the benchmark through.
        """
        messages = [
            ("system", self.log_reflection_prompt),
            (
                "human",
                (
                    f"Raw return: {raw_return:+.1%}\n"
                    f"Alpha vs {benchmark_name}: {alpha_return:+.1%}\n\n"
                    f"Final Decision:\n{final_decision}"
                ),
            ),
        ]
        return self.quick_thinking_llm.invoke(messages).content

    def reflect_on_imported_trade(
        self,
        decision_summary: str,
        realized_dollars: float,
        realized_return: float,
        technical_memory: str,
        technical_tags: list[str],
        fundamentals_context: str,
    ) -> str:
        """Reflection for imported execution-history trade records."""
        tags = ", ".join(technical_tags) if technical_tags else "none"
        messages = [
            ("system", self.import_reflection_prompt),
            (
                "human",
                (
                    f"Realized PnL (USD): {realized_dollars:+.2f}\n"
                    f"Realized return: {realized_return:+.2%}\n"
                    f"Technical memory summary: {technical_memory}\n"
                    f"Technical memory tags: {tags}\n\n"
                    f"Fundamentals Context:\n{fundamentals_context}\n\n"
                    f"Trade Summary:\n{decision_summary}"
                ),
            ),
        ]
        return self.quick_thinking_llm.invoke(messages).content
