# Backtest Harness Reasoning Plan

## Goal
Introduce a backtest-aware reasoning layer so risk decisions account for historical performance metrics.

## Scope
- Extend risk management agents to consume backtest metrics.
- Add a dedicated backtesting analysis component if needed.
- Store relevant backtest metrics in memory logs for downstream decisions.

## Implementation Steps
1. Define normalized metric schema (for example: Sharpe, max drawdown, win rate, sample size).
2. Extend risk management prompts and state inputs to include backtest metrics.
3. Implement sizing adjustment rules that map backtest quality to position sizing guidance.
4. Add optional agent/module to summarize backtest quality into risk-facing language.
5. Update memory logging to persist metric snapshots used during decisions.
6. Add tests for:
   - Metric ingestion and validation
   - Position-sizing adjustments
   - Missing/low-confidence metric handling

## Validation
- Risk outputs reflect backtest context when present.
- Behavior degrades gracefully when metrics are absent.
- Memory logs include metric references for auditability.

## Risks
- Historical overfitting can bias real-time decisions.
- Larger prompts/inputs may increase latency and cost.
