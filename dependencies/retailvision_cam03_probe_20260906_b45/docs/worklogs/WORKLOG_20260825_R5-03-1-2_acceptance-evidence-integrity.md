# Worklog: R5-03.1.2 Acceptance Evidence Integrity Fix

Date: 2026-08-25  
Status: `BLOCKED`  
Decision: `BLOCKED_R5_04_CANARY`

## Scope

This phase was limited to the Agent acceptance checker, evidence statistics,
non-overwrite behavior, and documentation. Product Analytics business
semantics and all frozen Offline, BehaviorEvent, Identity, Visit, Arrival, and
Publication components were left unchanged.

## Changes

- Replaced broad negation exemptions with clause-boundary handling and added
  Chinese/English contrast regressions.
- Added conservative product-to-metric binding for sentences, lists, tables,
  and English answers; swapped or unbound values fail closed.
- Added non-empty output-directory rejection with stable
  `acceptance_output_directory_not_empty` behavior.
- Added summary arithmetic validation and a post-run check tying the
  `real_agent_acceptance` suite to the emitted manifest.
- Required each scenario to have a successful result from its expected MCP
  tool before grounding the answer.
- Preserved prior evidence directories and recorded their SHA-256 values.

## Verification

- Checker tests: 19/19 passed.
- Product Analytics/MCP: 94/94 passed.
- Event 804 frozen regression: 1/1 passed.
- Offline/publication focused: 34/34 passed.
- Python full baseline: 1231 total, 1221 derived passed, 5 failures, 1 error,
  4 skipped; failures/errors are known Identity/device-recognition and import
  baselines.
- Java full baseline: 138 total, 134 derived passed, 1 known Windows
  HookEngine failure, 3 skipped.
- Real Agent: 7 scenarios, 23 attempts, 2 first-attempt passes, final 4/7 pass,
  3 grounding failures, 0 forbidden-field failures.

## Evidence

Current evidence is in
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_1_2_agent_acceptance_20260825\`. Its manifest, JSONL line count,
and test summary agree. Previous R5-03.1 and R5-03.1.1 evidence remains
historical/superseded and was not overwritten. Blocked retries are retained in
separate `*_blocked_attempt*` directories.

## Boundary Audit

No Stage5, Stage7C, Stage8, Stage9, tracking semantics,
`Stage9BehaviorFactAdapter`, ProductInteractionFact semantics, QueryService
口径, MCP tool meaning, BehaviorEvent, Identity Safe Projection, Visit,
Arrival, Publication Gate, Artifact Manifest/Seal, Publication Fence, or Event
804 geometry/threshold was modified.

## Next Step

Do not enter R5-04. A future rerun must provide seven real-agent final passes
with zero grounding and forbidden-field failures before changing the decision.
