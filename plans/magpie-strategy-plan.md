# Magpie Strategy Integration Plan

## Goal
Integrate Magpie signals into trader decision-making so recommendations combine analyst outputs with configurable strategy weights.

## Scope
- Extend trader decision logic in `tradingagents/agents/trader/trader_agent.py`.
- Support weighting Magpie signals against existing analyst/research signals.
- Preserve current `TraderProposal` structured output contract.

## Implementation Steps
1. Review current flow from `ResearchPlan` to `TraderProposal` and identify insertion point for Magpie inputs.
2. Define configuration keys for Magpie weighting and default behavior when Magpie signals are unavailable.
3. Add signal fusion logic that combines research recommendation and Magpie output into a unified action signal.
4. Update trader prompt/context so the model can explain how Magpie influenced the decision.
5. Add tests covering:
   - Magpie enabled and weighted
   - Magpie missing/failing (graceful fallback)
   - Boundary cases around conflicting signals

## Validation
- Unit tests pass for trader routing and signal aggregation.
- Generated `TraderProposal` remains schema-valid.
- Decisions are deterministic for fixed mock inputs.

## Risks
- Overweighting Magpie may cause abrupt behavior changes.
- Prompt changes could alter recommendation tone/consistency.
