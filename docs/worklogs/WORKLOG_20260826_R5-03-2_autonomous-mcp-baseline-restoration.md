# Worklog: R5-03.2 Autonomous MCP Baseline Restoration

Date: 2026-08-26
Status: `COMPLETED`

## What changed

- Removed `ProductAnalyticsBusinessAnswerPolicy` and its tests from the
  mewcode Java working tree.
- Removed its integration from `Agent.java`, `PrintMode.java`,
  `RemoteServer.java`, and `MewCodeModel.java`.
- Removed Product Analytics keyword recognition, expected-tool routing,
  trusted-scope regex parsing, Java alias defaults, deterministic final-answer
  templates, analytics stream suppression, special retry/token/rate-limit
  branches, and post-tool early termination.
- Restored the generic sequence: LLM selects an MCP tool, MCP returns
  structured data, and the LLM produces the final answer.
- Relabeled the three-batch runner as autonomous observation evidence without
  changing scenarios, checker rules, retry count, or pass criteria.
- Retained the Python MCP grounding contract, public-fact presentation rules,
  schema/query boundaries, and generic Java MCP lifecycle/error handling.

## Verification

- Focused Python Product Analytics/MCP/checker suite: `48/48 passed`.
- Focused Java MCP/lifecycle/integration suite: passed.
- Java package compilation without tests: passed.
- Full Python discovery: 1235 total, 1225 passed, 5 known
  Identity/device-recognition failures, 1 known device-identity import error,
  and 4 skipped.
- Full Java: 138 total, 134 passed, 1 known Windows/WSL HookEngine failure,
  and 3 skipped.
- Generic Java entry points contain no RetailVision identifiers, Product
  Analytics policy, product keys, or fixed routing helpers.

## Autonomous observation

The retained final observation at
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_2_autonomous_mcp_baseline_20260826_final\manifest.json` ran
three independent batches, one Agent invocation per question, with zero
retries. Results were `7/7`, `7/7`, and `5/7`, for `19/21` overall. Batch 3
missed tool selection for `rising-products` and `display-adjustment`; both
empty answers were retained as failures. The earlier autonomous observation
directory is also retained and was not used for sample selection.

## Boundary and decision

No ProductInteractionFact, QueryService algorithm/schema, MCP business schema,
Offline, Identity, Visit, Publication Gate, Manifest/Seal, or Event 804
threshold was changed by this restoration.

```text
R5_03_2_AUTONOMOUS_BASELINE_RESTORED
R5_04_DEVELOPMENT_ALLOWED
R5_04_PRODUCTION_ENABLEMENT_CONDITIONAL
```

R5-04 implementation work may proceed against the restored generic boundary.
Production enablement remains conditional because autonomous stability is
currently 19/21 rather than 21/21.
