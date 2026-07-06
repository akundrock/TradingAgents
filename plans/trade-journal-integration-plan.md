# Trade Journal Integration Plan

## Goal
Incorporate execution history into agent reasoning so recommendations can reference what actually happened after prior decisions.

## Scope
- Extend shared state with an `execution_history` field.
- Backfill state from trade journal records.
- Update agents and memory logging to use execution outcomes.

## Implementation Steps
1. Define `execution_history` structure (timestamp, instrument, action, size, price, outcome, notes).
2. Add state initialization and loading logic to hydrate history from journal data source.
3. Update relevant agent prompts to reference prior execution outcomes in current recommendations.
4. Add logic to correlate recommendation rationale with realized results.
5. Extend memory logging/reporting to include decision-to-outcome linkage.
6. Add tests for:
   - State hydration from journal
   - Prompt context includes execution history
   - Correlation output appears in logs/reports

## Validation
- Agents can reference prior trades without breaking existing flows.
- Reports contain traceable links between decisions and outcomes.
- No schema regressions in existing structured outputs.

## Risks
- Journal quality/inconsistency can inject noisy context.
- Large histories may require truncation/summarization policies.
