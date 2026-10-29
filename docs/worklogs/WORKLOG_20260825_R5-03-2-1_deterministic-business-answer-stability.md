# Worklog: R5-03.2.1 Deterministic Business Answer Stability Closure

Date: 2026-08-25
Status: `REJECTED` / `SUPERSEDED`

## Current status correction (2026-08-26)

This worklog records a completed experiment, not the current implementation.
Its 21/21 evidence remains historical validation evidence, but the architecture
was rejected because it coupled the generic mewcode Agent to RetailVision,
replaced autonomous LLM tool selection with Java routing, and replaced the LLM
final answer with deterministic Java output. R5-03.2 Autonomous MCP Baseline
Restoration removed that integration. See
`WORKLOG_20260826_R5-03-2_autonomous-mcp-baseline-restoration.md`.

## What changed

- Added the Java `ProductAnalyticsBusinessAnswerPolicy` and applied it at the
  shared Agent terminal-answer boundary, with PrintMode retaining a final
  output guard for CLI.
- Added policy tests for subjective popularity/interest, interaction
  comparison, multiplier questions, full public metrics, display priority,
  empty/error results, internal-field suppression, and no-tool answers.
- Removed internal reason-code and score wording from MCP tool descriptions.
- Tightened MCP instructions to prohibit unsupported ratios, percentages,
  people counts, purchase tendencies, and internal fields even for full-metric
  requests.
- Added `run_store_intelligence_business_answer_stability.py`, which requires
  exactly three independent batches and exactly one attempt per scenario.
- Added one-pass analytics routing/termination in the shared Java Agent. It
  derives canonical MCP arguments from trusted scope and aliases, suppresses
  model mis-selected analytics calls, and emits the policy answer immediately
  after facts arrive. Alias matching handles slash-separated public aliases.
- Closed semantic follow-ups: trend answers include every returned product,
  empty anomaly output has anomaly-rule semantics, display actions come from
  public returned fields, and analytics retry branches are disabled.
- Analytics provider timeout/error events now terminate with and persist the
  stable tool-error template without exposing raw provider text.

## Why

R5-03.2 hard-grounding tests were green, but real Agent final prose varied
between 2/7 and 6/7. A deterministic final-answer boundary was needed without
changing ProductInteractionFact, QueryService algorithms, MCP business fields,
or frozen Offline/Identity/Visit/Publication components.

## Verification

- Focused Python MCP/checker tests: `48/48` after the contract wording update.
- Focused Java policy/MCP/lifecycle tests: passed.
- Full Python: `1225/1235` passed, with the known `5` failures, `1` import
  error, and `4` skips documented in the qualification report.
- Real Agent stability: final13 evidence `21/21`, three batches `7/7`, zero
  retries and 24 underlying MCP calls for 21 Agent invocations.
- Full Java: `146/150` passed, with the known Windows HookEngine baseline
  failure and `3` skips; focused Java policy/MCP tests passed.

## Historical evidence

Final evidence:
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_2_1_business_answer_stability_20260825_final13\manifest.json`

The prior `18/21`, interrupted, and superseded directories remain read-only
failure samples. The recorded experimental decision was
`R5_03_2_1_PASS` / `READY_FOR_R5_04_CANARY`; it is superseded and is not a
current production-enablement decision.
