# Worklog: MB-01A Offline Member Behavior

Date: 2026-08-27
Status: `CODE LOOP COMPLETE / CONTROLLED POLICY REVIEW PASS / REAL EVIDENCE PENDING`

## Evidence Boundary

**CONTROLLED FIXTURE — NOT REAL MEMBER EVIDENCE**

The repository currently contains no eligible real Offline member behavior
publication. Event 804 is `unmatched`; demo greeting rows, legacy live rows,
artifacts, and hand-written production rows are not MB-01 evidence. Focused
verification used temporary SQLite databases only. `production_database_written`
is `false`; `real_member_evidence` remains `pending`.

## Implemented

- Added a shared read-only `MemberBehaviorQueryService` and scope policy.
- Enforced active-member exact ID/name resolution and ambiguity errors.
- Enforced Offline-only confirmed identity and pickup/holding/put-down allowlist.
- Added exact product-name filtering, safe response-local product references,
  product grouping, holding-duration quality, and safe projections.
- Added summary and timeline MCP adapters with strict schemas, limit/has_more,
  stable ordering, timezone conversion, and structured errors.
- Added independent Product Analytics and Member Behavior feature registration,
  fail-closed schema readiness, read-only SQLite startup, and disabled-tool
  result errors without blocking MCP initialize/tools/list.
- Added `RETAILVISION_TIMEZONE`, required only when Member Behavior is enabled.

## Validation

- Smoke checker focused tests: `24/24 passed`.
- MB-01 service, adapter, and server focused tests: `34/34 passed`
  (`16 + 5 + 13`).
- Product Analytics schema-readiness and MCP server regression: `30/30 passed`
  (`13 + 17`). The combined focused command therefore passed `88/88`.
- Controlled temporary SQLite MCP smoke: Member-only `tools/list` returned 2
  tools; summary call matched fixture facts; forbidden internal fields absent.
- `python -m compileall -q agent test`: passed.
- Autonomous Agent development smoke produced six retained diagnostic batches
  while the Windows JDK Unix-domain socket condition and checker were being
  diagnosed. These batches are not a qualifying no-retry acceptance run and
  must not be combined or relabeled as one. The latest batch is `0/2`: the
  summary scenario made no Member tool call and the timeline scenario called
  both tools. All evidence roots are retained as
  `reports/mb01_controlled_fixture_20260827_run{1..6}/` and remain blocked.
- Review remediation deterministic tests are included in the reproducible
  `88/88` focused result above. One fresh, versioned
  acceptance batch was executed as
  `reports/mb01_controlled_fixture_20260827_run7/`; both scenarios are
  `BLOCKED` with one process invocation each, zero retries, and no accepted
  Member tool call. Its retained stderr proves Java `OpenAiCompatClient`
  initialization failed with `Unable to establish loopback connection` before
  ToolSearch, MCP Server startup evidence, or tool selection. This batch is
  retained separately and is not merged with runs 1-6.
- The updated checker replayed run7 offline without invoking Java or the
  model. Both scenarios remain `BLOCKED` (`1` recorded process invocation,
  `0` retries, `0` Member tool calls). The run7 directory contains no separate
  MCP Server log, so no MCP tool-selection or product-answer claim is inferred.
- Run7 root-cause evidence is sufficient and is recorded as
  `RUN7_AUTONOMOUS_ACCEPTANCE_BLOCKED`: both retained stderr streams fail while
  Java constructs `java.net.http.HttpClient` (`OpenAiCompatClient.java:64`),
  before ToolSearch, MCP startup, or model selection. The configured provider
  is `https://api.deepseek.com` (host `api.deepseek.com`, default HTTPS port
  `443`) from the external `.mewcode/config.yaml`; it is not a localhost
  service. The JDK 26 stack fails in `PipeImpl` / `UnixDomainSockets` with
  `Invalid argument: connect`, so no provider listener or local MCP listener
  was involved and no service-down conclusion is justified. Java and the
  provider are not separate local host environments in this run.
- The active default runtime reported vendor `Oracle Corporation`, version
  `26.0.2`. The external Java project
  declares `maven.compiler.release=21`; JDK 17 is below its build baseline. A
  temporary Eclipse Adoptium Temurin `21.0.12.1 LTS` runtime was used for one
  minimal `HttpClient.newHttpClient()` probe. The probe reused the accepted
  R5-04.2 process-local workaround
  `-Djdk.net.unixdomain.tmpdir=D:\GJJ\work\java-unix-domain-sockets`, printed
  `HTTP_CLIENT_PROBE_PASS`, and exited `0`. It did not contact the Provider or
  start MCP. The effective probe command was
  `java.exe -Djdk.net.unixdomain.tmpdir=D:\GJJ\work\java-unix-domain-sockets HttpClientProbe.java`
  using the temporary Temurin 21 binary.
- After that successful probe, exactly one new versioned Agent batch was run
  at `reports/mb01_controlled_fixture_20260827_run8/`. Its original files remain
  immutable
  `0/2 BLOCKED`, with one process invocation per scenario and zero retries.
  Java startup, ToolSearch, MCP discovery, and Member MCP calls succeeded. The
  original exact-single-tool policy blocked summary because it called both
  related read-only Member tools. The timeline scenario called only its
  expected tool. No further Agent batch was started.
- The frozen acceptance policy now permits composition of the two Member
  Behavior read-only tools, each at most once, while requiring the scenario's
  expected tool, successful calls, one member, one resolved scope, safe public
  projections, and facts grounded across all returned payloads. The independent
  `run8_policy_review.json` re-evaluated the untouched run8 streams under that
  policy without invoking Agent, Java, MCP, or the model. Both scenarios pass;
  the review records `RUN8_POLICY_REVIEW_PASS` and hashes of the immutable raw
  evidence.
- Full repository discovery: 1298 tests, 5 failures, 1 import error, and 4
  skips. The non-green cases remain the existing unrelated
  device-recognition/provider baseline; no MB-01 failure was observed.

## Current Status

```text
MEMBER_BEHAVIOR_QUERY_GATE_PASS
MB_01_CODE_LOOP_COMPLETE
AUTONOMOUS_MCP_BOUNDARY_PRESERVED
REAL_MEMBER_EVIDENCE_PENDING
NOT_FULLY_CLOSED
```

`MEMBER_BEHAVIOR_QUERY_GATE_PASS`, `MB_01_CODE_LOOP_COMPLETE`, and
`AUTONOMOUS_MCP_BOUNDARY_PRESERVED` describe controlled-fixture verification
and the independent run8 policy review only. Do not record `MB_01_CLOSED`,
`PRODUCTION_VALIDATED`, or `REAL_EVIDENCE_PASS` until a formal real Offline Run
satisfies the frozen identity/publication and Agent smoke requirements.

## Next Entry

The Java loopback blocker is resolved for the supported temporary JDK 21
runtime by the established R5-04.2 process-local socket directory. Run8 proves
discovery and MCP execution; its immutable original result remains `BLOCKED`,
while the independent composition-policy review passes both scenarios without
an Agent rerun. MB-01A checker work stops here. The remaining gate is formal
real Offline confirmed-member behavior evidence.
Future MB-01B/MB-01C should compose purchase, visit, and preference facts in
separate tools rather than widening these behavior responses.
