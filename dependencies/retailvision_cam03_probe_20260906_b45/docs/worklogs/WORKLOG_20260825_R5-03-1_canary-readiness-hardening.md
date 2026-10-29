# Worklog: R5-03.1 Canary Readiness Hardening

Date: 2026-08-25  
Status: `COMPLETED`

## Scope

Closed the R5-03 canary-readiness findings without extending the anonymous
Product Analytics model or changing formal BehaviorEvent, Identity, Visit,
Arrival, Publication Gate, Stage5, Stage7C, Stage8, or Stage9 behavior.

## Changes

- Removed implicit `evolve_schema` from MCP composition. Disabled MCP does not
  touch the database; enabled MCP performs read-only schema compatibility
  inspection and fails with `product_analytics_schema_incompatible`.
- Added the explicit `python -m agent.mcp_server.migrate` operator command,
  which reuses the existing evolution path.
- Added an isolated deterministic fixture using Stage9 normalization and
  `ProductInteractionFactWriter`; Java tests no longer copy `retailvision.db`
  or rely on a hardcoded successful checkout.
- Added required acceptance mode, stable Java MCP error codes, one-shot manager
  lifecycle state, process cleanup, timeout, and HTTP transport coverage.
- Clarified the metadata distinction between product-priority recommendation
  and priority-based display action without claiming physical layout knowledge
  or changing either tool's business logic.
- Added a real seven-question Agent runner with sanitized JSONL and manifest
  evidence, fail-closed numeric/code/semantic grounding checks, forbidden-data
  redaction, and bounded transparent retries.

## Verification

- Product Analytics/MCP/schema focused: 53/53 passed.
- MCP schema-readiness hardening: 7/7 passed.
- Event 804: 1/1 passed with frozen metrics and zero BehaviorEvent rows.
- Offline behavior/publication focused: 34/34 passed.
- Java normal focused mode: 13 tests, 11 passed, 2 integration unavailable
  skips when cross-repository environment variables were absent.
- Java required mode: 13/13 passed, 0 skipped.
- Required-mode missing-checkout probe: 2 failures, 0 skipped, as required.
- Real Agent acceptance: 7/7 passed, 9 total attempts, 1 retried scenario,
  0 forbidden-field failures using `deepseek/deepseek-chat`.
- Python full: 1219 total; 1209 passed, 5 known failures, 1 known error,
  4 skipped.
- Java full: 136 total; 132 passed, 1 known Windows HookEngine failure,
  3 skipped.

## Decision

`READY_FOR_R5_04_CANARY`

Full report:
The repository report is retained in the external archive under
`historical-docs/docs/repository/R5_03_1_CANARY_READINESS_HARDENING_REPORT.md`.

Sanitized Agent evidence:
The acceptance manifest is retained in the external archive under
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_1_agent_acceptance_20260825\manifest.json`.

## Next Step

Keep rollout fixed to `store-1/cam02`, run migration as a separate operator
step, and monitor stable MCP error codes, grounded answers, tool-selection
drift, and child-process termination before expanding scope.
