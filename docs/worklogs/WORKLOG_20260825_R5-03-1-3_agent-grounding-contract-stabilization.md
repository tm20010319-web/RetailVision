# Worklog: R5-03.1.3 Agent Grounding Contract Stabilization

Date: 2026-08-25  
Status: `COMPLETED`  
Decision: `READY_FOR_R5_04_CANARY`

## Scope

This phase stabilized the Agent response contract through MCP initialize
instructions and tool descriptions. It did not change the grounding checker,
the seven acceptance questions, the acceptance fixture, Agent tool-selection
logic, Product Analytics schemas, QueryService semantics, or frozen business
components.

## Root Causes and Fixes

- `toy-a-summary`: the Agent used “匿名追踪数”, which was not the approved
  `unique_person_track_count` label and could not be metric-bound. The shared
  contract now requires the exact label `匿名轨迹数`.
- `rising-products`: the Agent added interest/heat wording and unrelated
  holding/recommendation commentary. Trend descriptions now require only
  `interaction_trend` and explicitly labeled current/previous interaction
  counts.
- `display-adjustment`: the Agent added physical-placement caveats and
  recommendation reason codes. The display description now requires one
  product per line with the returned display action and omits reason codes or
  physical-placement language.
- Diagnostic runs also exposed table/transposed-metric ambiguity. Rank and
  comparison descriptions now prohibit those formats. Follow-up real-agent
  retries exposed score and cross-product summary prose in comparison/display
  answers; those descriptions now explicitly omit scores, reason codes, and
  cross-product summaries.

## Verification

- MCP server/schema focused: 20/20 passed.
- Agent checker: 19/19 passed.
- Post-hardening combined focused rerun: 39/39 passed.
- Single-attempt diagnostic: 7 scenarios, 3 passed, 4 grounding failures,
  0 forbidden failures, 7 total attempts.
- Formal acceptance (initial directory retained): 7/7 passed, 14 total
  attempts, 3 first-attempt passes, 4 retried scenarios, 0 grounding failures,
  0 forbidden failures.
- Revalidated acceptance: 7/7 passed, 9 total attempts, 5 first-attempt passes,
  2 retried scenarios, 0 grounding failures, 0 forbidden failures.

Evidence paths:

- Diagnostic: `D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_1_3_agent_diagnostic_20260825\`
- Formal (historical initial): `D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_1_3_agent_acceptance_20260825\`
- Formal (current revalidation): `D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_1_3_agent_acceptance_20260825_retry3\`

## Boundary Audit

No Stage5, Stage7C, Stage8, Stage9, tracking semantics,
`Stage9BehaviorFactAdapter`, ProductInteractionFact semantics, QueryService
口径, MCP input/output schema, BehaviorEvent, Identity Safe Projection, Visit,
Arrival, Publication Gate, Artifact Manifest/Seal, Publication Fence, or Event
804 geometry/identity threshold was modified.

## Next Step

R5-04 canary enablement may proceed with first-attempt rate and real-LLM
grounding drift retained as explicit monitoring signals.
