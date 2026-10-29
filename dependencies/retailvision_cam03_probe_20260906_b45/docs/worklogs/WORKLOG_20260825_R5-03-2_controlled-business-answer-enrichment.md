# Worklog: R5-03.2 Controlled Business Answer Enrichment

Date: 2026-08-25  
Status: `BLOCKED`  
Decision: `BLOCKED_R5_04_CANARY`

## Scope

R5-03.2 separates hard safety boundaries from safe deterministic business
interpretations. It changes only MCP instructions, tool descriptions, the
expression-level grounding checker, tests, documentation, and a one-shot
business-answer smoke runner.

## Changes

- Added layered contract constants for fact labels, hard boundaries, safe
  derived comparisons, and concise presentation guidance.
- Removed full-contract duplication from all seven tool descriptions.
- Permitted metric-grounded comparisons, rank/trend summaries, display-priority
  conclusions, concise answers, safe user-question rephrasing, and two-decimal
  score rounding.
- Preserved rejection of commerce, identity/visitor, subjective interest or
  engagement claims, physical placement, fabricated values, and contradictory
  recommendation/trend conclusions.
- Added `scripts/run_store_intelligence_business_answer_smoke.py` for seven
  real-Agent questions with one attempt per question and isolated Fixture.

## Verification

- Focused Product Analytics/MCP/schema/runtime/Agent tests: `75/75 passed`.
- Full Python discovery: `1235` total, `1225` derived passed, `5` known
  Identity/device-recognition/durable-push failures, `1` existing import error,
  and `4` skipped. The four-test increase is from this phase's new checker
  coverage; no new Product Analytics/Offline failure appeared.
- Business smoke runs were retained without overwriting:
  - initial: 4/7;
  - retry1: 5/7;
  - retry2: 5/7;
  - retry3: 4/7;
  - retry4: 6/7;
  - retry5: 2/7;
  - latest retry6: 5/7.

The safety contract is passing, but the real Agent still intermittently emits
unsupported “more people” or derived “three times” phrasing for subjective
questions.
This is a business-answer experience block, not a reason to weaken the
checker or fabricate evidence.

## Evidence

Latest manifest:
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_2_business_answer_smoke_20260825_retry6\manifest.json`

Qualification report:
`docs/repository/R5_03_2_CONTROLLED_BUSINESS_ANSWER_ENRICHMENT_REPORT.md`

## Next Step

Keep R5-04 blocked. Investigate deterministic prompt/tool-routing behavior for
subjective and full-metrics requests, then rerun a bounded independent smoke
set. Do not alter ProductInteractionFact, QueryService, MCP schemas, or frozen
Offline/identity/publication components.
