# Worklog: R5-02 Product Analytics Controlled Enablement

Date: 2026-08-25  
Status: `COMPLETED`

## Scope

Added and qualified default-off controls for anonymous Product Analytics
projection and MCP reads. Core ProductInteractionFact semantics were retained;
formal BehaviorEvent, Identity, Visit, Arrival, Publication Gate, and
Stage5/7C/8/9 were not changed.

## Changes

- Added strict default-false projection and MCP flags.
- Required a deployment HMAC secret at the Offline projection composition root.
- Required sealed artifact, verified source/registration scope, and
  authoritative time before projection.
- Kept identity unmatched independent from anonymous analytics eligibility.
- Added distinct projection status/reason and safe writer audit counts.
- Added 31-day query cap, stable limit error, and StoreContext store/camera
  isolation.
- Added stable MCP disabled behavior and removed unexpected exception detail
  from MCP responses.
- Preserved the sole read path through QueryService.

## Verification

- Combined Product Analytics/MCP/harness/schema/pipeline: 67/67 passed.
- Offline behavior/publication/identity/pipeline: 101/101 passed.
- Event 804: 1/1 focused acceptance passed with six rows and expected metrics.
- MCP server: 11/11 passed; actual stdio process completed all seven tools.
- Full repository: 1203 tests, 5 failures, 1 error, 4 skipped. The six
  non-success outcomes match known Identity/device-recognition baselines.
- `git diff --check`: passed; only existing line-ending conversion warnings.

## Operations

Projection and MCP have independent kill switches. Disabling them retains data
and leaves formal processing active. Same-Run replay is idempotent; conflicting
content is rejected and reported separately from formal publication failures.

Full report:
The repository report is retained in the external archive under
`historical-docs/docs/repository/R5_02_PRODUCT_ANALYTICS_CONTROLLED_ENABLEMENT_REPORT.md`.

## Next Step

Run one-store/one-camera limited projection, then enable scoped MCP reads and
perform external mewcode-java Agent acceptance before any rollout expansion.
