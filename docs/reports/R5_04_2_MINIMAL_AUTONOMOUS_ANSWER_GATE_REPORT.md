# R5-04.2 Minimal Autonomous Answer Gate Closure Report

Date: 2026-08-26  
Classification: `VALIDATION_EVIDENCE`  
Status: `CODE_COMPLETE / ACCEPTANCE_BLOCKED`

## Decision

```text
R5_04_2_GATE_CLOSURE_IMPLEMENTED
AUTONOMOUS_MCP_BOUNDARY_PRESERVED
R5_04_2_GATE_BLOCKED_BY_AUTONOMOUS_ACCEPTANCE
```

`R5_04_2_GATE_CLOSED` and `READY_FOR_R5_05` are not met. No Java Agent,
OpenAiCompatClient, McpManager, PrintMode, RemoteServer, TUI, or SubAgent code
was changed.

## Code closure

- Corrected `GLOBAL_PRESENTATION_GUIDANCE` sentence ordering.
- Enabled MCP startup now requires a valid product-catalog profile and fails
  closed with `product_catalog_required`; unknown products remain
  `product_key_unknown` and cannot produce zero-filled normal results.
- Split blocking `presentation_checks` from non-blocking
  `presentation_warnings`. Only generic empty-tool-result wording without a
  tool name or internal detail is a warning.
- Retained blocking anonymous-person wording, internal product keys, tool/MCP
  execution details, raw scores/reasons, and unsupported intent claims.
- Fixed grounding checker bindings observed in immutable recovery evidence:
  single-dwell wording, returned recommendation attention wording, and ISO
  scope timestamps immediately followed by Chinese text.
- Aligned `compare_product_behavior` JSON Schema with its runtime contract:
  both two-product and two-window modes now publish their required fields.

Focused Python MCP/acceptance/readiness tests: **72/72 passed**. Python
compilation and changed-file `git diff --check` passed.

Python full suite: **1267 tests, 5 failures, 1 error, 4 skipped**. This matches
the recorded baseline failure shape (5 failures, 1 error, 4 skipped); all 36
tests added since the 1231-test baseline passed. The retained failures are in
device-recognition identity flows, and the retained import error is the known
`initialize_schema` removal mismatch. No R5-04.2 regression remains in the
full suite.

The unchanged Java repository passed the strict cross-repository focused set:
**25/25 passed** (`OpenAiCompatToolCallingTest`, `McpManagerTest`, and
`RetailVisionMcpIntegrationTest`). Java full suite: **150 tests, 1 failure,
0 errors, 3 skipped**; the sole failure remains the recorded Windows
`HookEngineTest.testCommandInjectsFilePathEnvVar` baseline. Both Java runs used
the recovered JDK AF_UNIX socket directory.

## JVM diagnosis and recovery

The original 21 `AGENT_PROCESS_FAILED` records were caused before Provider or
MCP traffic by Java selector initialization:

```text
HttpClient.newBuilder().build()
 -> PipeImpl / UnixDomainSockets.connect0
 -> SocketException: Invalid argument: connect
```

The fault was narrowed to AF_UNIX endpoints created under the current user's
`%TEMP%` directory. Binding succeeded, connecting failed with `EINVAL`, and
unusable `socket_*` reparse points remained. The same JDK succeeds when the
JDK-specific socket directory is moved outside that Temp tree:

```text
-Djdk.net.unixdomain.tmpdir=D:\GJJ\work\java-unix-domain-sockets
```

This is not a RetailVision, Provider-authentication, or Java Agent routing
failure. It also explains why changing only `java.io.tmpdir` did not help:
Windows JDK AF_UNIX selection checks `jdk.net.unixdomain.tmpdir`, then `%TEMP%`,
before `java.io.tmpdir`.

Historical evidence did not capture its JDK/socket-directory state, so the
exact reason the Temp directory worked earlier cannot be proven. The supported
conclusion is that its AF_UNIX behavior changed or became corrupted between
the prior successful run and R5-04.2, not that R5-04.2 code disabled the JVM.

## Immutable recovery evidence

No evidence directory was overwritten and the historical R5-04.1 `final8`
JSONL remains unchanged.

| evidence | pass | protocol | security | grounding | presentation | retries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `r5_04_2_minimal_autonomous_answer_gate_20260826` | 0/21 | 0/21 | blocked | blocked | blocked | 0 |
| `..._recovery1` | 18/21 | 21/21 | 21/21 | 18/21 | 20/21 | 0 |
| `..._recovery2` | 17/21 | 21/21 | 21/21 | 17/21 | 21/21 | 0 |
| `..._recovery3` | 17/21 | 20/21 | 21/21 | 19/21 | 19/21 | 0 |

All recovery runs contain exactly 3 batches, 7 scenarios per batch, 21 Agent
invocations, one invocation per scenario, and zero hidden retries. All record
zero stream-parse failures, zero argument-assembly failures, and zero unknown
product keys.

The latest immutable `recovery3` failures are:

- batch 1 `display-adjustment`: leaked `smart_toy_a` and `smart_toy_b`.
- batch 2 `display-adjustment`: exposed tool execution wording.
- batch 2 `rising-products`: checker rejected `保持关注`; the post-run focused
  fix is covered by tests but the historical JSONL is not rewritten.
- batch 3 `compare-toys`: the first tool call omitted required time fields and
  returned `start_at_required`; the model then corrected itself within the same
  invocation. Acceptance correctly retains the earlier MCP failure. The schema
  mismatch that invited this call is fixed after the run.

Because two real presentation failures remain in `recovery3`, another run was
not started merely to seek a favorable sample. A future qualification must run
one fresh complete 3x7 set after the final checker/schema changes and must meet
all gates without retries or sample replacement.

## Remaining blockers

```text
R5_04_2_GATE_CLOSED = NOT MET
READY_FOR_R5_05 = NOT MET
PRODUCTION_ENABLEMENT_READY = NOT MET
```

The remaining evidence blocker is autonomous answer consistency, specifically
internal-key/execution-process presentation leakage, plus the absence of one
fresh post-schema-fix 21-scenario qualification. The autonomous MCP boundary
remains intact.
