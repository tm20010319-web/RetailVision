# RetailVision Product Analytics MCP Server

The production boundary is:

```text
mewcode-java Agent
  -> mewcode MCP Client
  -> RetailVision MCP Server (stdio)
  -> ProductAnalyticsMCPTools
  -> ProductAnalyticsQueryService
  -> product_interaction_facts
```

The Python `ProductAnalyticsAgentRuntime` remains a local Tool Calling
Harness. It is not part of this production path.

## Migrate, then start

Schema evolution is an explicit operator action and is never part of MCP
server composition:

```powershell
python -m agent.mcp_server.migrate --database-url "sqlite:///D:/path/to/retailvision.db"
python -m agent.mcp_server.server --database-url "sqlite:///D:/path/to/retailvision.db"
```

The migration command reuses the repository's existing `evolve_schema`; there
is no MCP-specific migration implementation. Run it under the normal database
change procedure before enabling the MCP process.

The MCP command only inspects the Product Analytics schema and queries facts.
It does not create tables, add columns, add indexes, or call `evolve_schema`.
For SQLite, every runtime connection also enables `PRAGMA query_only=ON`.
Missing database files and missing/incompatible Product Analytics columns,
indexes, or unique keys fail startup with
`product_analytics_schema_incompatible`; a missing SQLite file is not created.

## Start

From `D:\GJJ\work\code`:

```powershell
$env:PYTHONUTF8 = "1"
$env:RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED = "true"
$env:RETAILVISION_STORE_ID = "store-1"
$env:RETAILVISION_CAMERA_ID = "cam02"
$env:RETAILVISION_ANALYSIS_START_AT = "2026-08-20T13:00:00"
$env:RETAILVISION_ANALYSIS_END_AT = "2026-08-20T14:00:00"
$env:RETAILVISION_PRODUCT_CATALOG_PROFILE = "stage10_smart_toys_v1"
python -m agent.mcp_server.server --database-url "sqlite:///D:/GJJ/work/code/retailvision.db"
```

`RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED` is `false` by default. Invalid
boolean values fail startup. When disabled, `initialize` remains available but
`tools/list` and `tools/call` return the stable JSON-RPC error
`product_analytics_mcp_disabled`.

Disabled composition does not construct a database engine, inspect schema, or
create/modify a database file. This makes the MCP flag a database-safe kill
switch as well as a tool-discovery switch.

The server uses MCP stdio because `mewcode-java (3)` already provides
`StdioClientTransport`; protocol messages are newline-delimited JSON-RPC on
stdin/stdout and diagnostics go to stderr.

The database configuration is resolved in this order:

1. `--database-url` command-line argument
2. `RETAILVISION_DATABASE_URL`
3. `DATABASE_URL`
4. local fallback `sqlite:///retailvision.db`

The fallback is for a pre-migrated local RetailVision runtime. It is not
created automatically. An actual production database is selected by the
deployment's command-line argument or environment configuration.

## mewcode config

Add this entry to `.mewcode/config.yaml`:

```yaml
mcp_servers:
  - name: retailvision
    command: python
    args:
      - -m
      - agent.mcp_server.server
    request_timeout_seconds: 60
    env:
      PYTHONUTF8: "1"
      RETAILVISION_DATABASE_URL: ${RETAILVISION_DATABASE_URL}
      RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED: "true"
      RETAILVISION_STORE_ID: "store-1"
      RETAILVISION_CAMERA_ID: "cam02"
      RETAILVISION_ANALYSIS_START_AT: "2026-08-20T13:00:00"
      RETAILVISION_ANALYSIS_END_AT: "2026-08-20T14:00:00"
      RETAILVISION_PRODUCT_CATALOG_PROFILE: "stage10_smart_toys_v1"
```

Use a deployment-owned window appropriate for the canary; the values above
are the qualified Event 804/local analytics window, not a global default.
`PYTHONUTF8=1` is required for Windows stdio so product display names and
aliases remain intact. The catalog profile maps the established canary names
`smart_toy_a`/`smart_toy_b` without asking the user or LLM to invent keys.

During `initialize`, the server publishes instructions generated from the same
`StoreContext` used to enforce queries. They tell the Agent the trusted
store/camera/window, catalog aliases, tool routing, empty/error behavior, and
forbidden raw/identity access. The server remains authoritative: an LLM-supplied
foreign store or camera is rejected even if the prompt asks for it.

The `McpManager` starts the process and performs the MCP lifecycle:

1. send `initialize` with the client's protocol version;
2. receive the negotiated `2024-11-05` or `2025-06-18` version and server
   capabilities;
3. send `notifications/initialized`;
4. call `tools/list` and register the returned schemas; and
5. call a selected tool with `tools/call`.

`request_timeout_seconds` is optional in mewcode and defaults to 60. Java
surfaces only stable MCP error codes plus a sanitized server name:

- `mcp_connection_failed`
- `mcp_request_timeout`
- `mcp_tool_call_failed`
- `mcp_manager_closed`
- `mcp_manager_already_connected`

SDK exception messages, local paths, commands, URLs, and environment values are
not returned to the Agent. Existing RetailVision business errors such as
`product_analytics_mcp_disabled` remain distinguishable. A timeout or
connection failure registers no RetailVision tools; the Agent must report
analytics unavailable rather than answer from files or prior assumptions.

One `McpManager` may connect only once. Repeated connection attempts fail
stably, `close()` is idempotent, and a closed manager cannot reconnect. Failed
initialize/discovery and timed-out stdio requests close the client and
terminate the child process. Create a new manager for a new connection.

The server rejects `tools/list` and `tools/call` before initialization. The
LLM in mewcode selects a tool; no Python keyword router is involved.

## Product catalog boundary

`agent/product_catalog.py` is the single product-key resolver used by the MCP
adapter. Deployment configuration supplies public aliases and canonical keys.
Every tool path accepting `product_key` or `second_product_key` resolves the
value before calling `ProductAnalyticsQueryService`. Canonical keys remain
valid, public aliases resolve to their canonical key, and unknown/empty values
fail closed with `product_key_unknown`. An unknown key therefore cannot produce
a normal response with `product_name=null` and all-zero metrics.

This catalog is a RetailVision Python MCP concern. No alias, product key, or
catalog profile is copied into the generic mewcode Java Agent.

## Tools

- `rank_products`: rank anonymous product interactions in a store and time
  window.
- `get_product_behavior_summary`: summarize anonymous interactions for one
  product.
- `compare_product_behavior`: compare two products or two time windows using
  returned interaction and holding metrics; safe metric comparisons are allowed.
- `recommend_hot_products`: return products recommended for display priority
  using deterministic returned interaction facts; no physical location advice.
- `analyze_product_trend`: analyze one product's trend between the current and
  previous window.
- `detect_product_anomaly`: report products satisfying the tool's deterministic
  anomaly rule and expose only returned trigger metrics.
- `suggest_product_display`: return increase, maintain, or decrease display
  priority from returned metrics; it cannot recommend a physical location.

The two display tools are deliberately distinct: "推荐重点陈列哪些商品" routes
to `recommend_hot_products`; "商品应该怎样调整陈列" routes to
`suggest_product_display`.

## Tool schemas

The server's `tools/list` response is projected from
`ProductAnalyticsMCPTools.list_tools()`. Each entry contains the tool name,
description, and a JSON Schema `inputSchema`; the server does not maintain a
second schema catalogue.

All schemas reject unknown properties. Timestamp fields use naive ISO-8601
local time, without a timezone suffix. `limit` fields accept integers from 1
through 100 and default to 10 where present. Query windows may not exceed 31
days. The configured `StoreContext` rejects an explicit foreign store and, when
a default camera is configured, rejects an explicit foreign camera.

The projection writer has an independent default-off switch:

```powershell
$env:RETAILVISION_PRODUCT_ANALYTICS_PROJECTION_ENABLED = "true"
$env:RETAILVISION_PRODUCT_ANALYTICS_TRACK_HASH_SECRET = "<deployment secret>"
```

The Offline runner also requires `--db-url` when projection is enabled. The
writer is composed only when the switch is true and writes only after a sealed,
source-verified, registration-scoped run with authoritative absolute time.
Turning either switch back to `false` is the kill switch; it does not delete
projection data or alter formal BehaviorEvent, Identity, Visit, or Publication
Gate processing.

`rank_products`, `get_product_behavior_summary`, and the explicit-scope mode
of `compare_product_behavior` require `store_id` and their required time or
product fields. `compare_product_behavior` also supports a two-product mode
using `product_key`, `second_product_key`, `start_at`, and `end_at`.

`recommend_hot_products`, `analyze_product_trend`, `detect_product_anomaly`,
and `suggest_product_display` accept optional `store_id`, `camera_id`,
`start_at`, and `end_at`. Missing scope values are resolved by `StoreContext`;
`analyze_product_trend` still requires `product_key`.

## Trace

```text
User: 最近哪些商品最火？
  -> mewcode LLM tool_call: rank_products
  -> MCP Client tools/call
  -> RetailVision MCP Server
  -> ProductAnalyticsMCPTools
  -> ProductAnalyticsQueryService
  -> Database
  -> MCP tool_result
  -> mewcode LLM final answer
```

## Cross-repository acceptance

Normal Java unit mode may mark the integration unavailable when the external
checkout is not configured. Required mode must not skip:

```powershell
$env:RETAILVISION_REPO = "D:\GJJ\work\code"
$env:RETAILVISION_ACCEPTANCE_REQUIRED = "true"
mvn -q "-Dtest=McpManagerTest,RetailVisionMcpIntegrationTest" test
```

`RetailVisionMcpIntegrationTest` creates a new temporary SQLite database on
every run and populates it through the explicit schema path, Stage9 adapter,
and `ProductInteractionFactWriter`. It never reads or copies the repository
root `retailvision.db`.

The seven real Agent scenarios are run separately and emit sanitized evidence:

```powershell
python scripts/run_store_intelligence_agent_acceptance.py `
  --java-repo "D:\path\to\mewcode-java" `
  --provider-config "D:\path\to\mewcode-java\.mewcode\config.yaml" `
  --output-dir "reports\r5_03_1_3_agent_acceptance_20260825_retry1" `
  --test-summary-source "reports\r5_03_1_2_test_summary_source.json"
```

Provider credentials and temporary database configuration are used only in a
temporary config and are never written to the evidence JSONL/manifest.
The runner fails closed when an answer introduces unsupported commerce,
identity/Visit, intent, interaction-quality, or physical-placement claims;
reports a number or structured code absent from tool results; or contradicts a
structured recommendation. If forbidden data is detected, stored arguments
are cleared and the answer is replaced with a fixed redaction marker.

R5-03.1.3 formal evidence is written to
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_03_1_3_agent_acceptance_20260825_retry3\` with checker
`r5-03-1-2-grounding-v3`. The checker also rejects unsupported attention,
engagement scale/depth/quality, interest, attractiveness, and deep-experience
claims while allowing explicit negated boundary statements. The prior
R5-03.1.2, R5-03.1.1, and R5-03.1 evidence directories are
historical/superseded and are not current acceptance proof. The runner refuses
non-empty output directories and verifies that the real-agent test summary
matches the emitted manifest, so historical evidence cannot be overwritten or
relabeled as a pass.

## Agent grounding contract

`agent/mcp_server/grounding_contract.py` separates the contract into four
layers: `FACT_LABELS`, `HARD_GROUNDING_BOUNDARY`,
`SAFE_DERIVED_INTERPRETATIONS`, and `GLOBAL_PRESENTATION_GUIDANCE`. The server
instructions publish all four layers; each tool description publishes only its
own routing, supported conclusions, and specific limits. The hard boundary
still forbids commerce, identity/people, subjective interest, and physical
placement inference. Safe deterministic comparisons, interaction-heat ranking,
trend direction, and display-priority conclusions are allowed when they follow
returned facts. The Agent should lead with a concise conclusion and only expand
to all raw fields when explicitly requested.

The acceptance checker remains a fact-consistency checker: it verifies returned
numbers, product/metric binding, safe comparison direction, recommendation and
trend consistency, and forbidden-field boundaries. It allows natural-language
synonyms, two-decimal score rounding, user-question quotation followed by a
safe reframe, and explicit boundary statements; it does not allow a negation to
be followed by a forbidden inference.

## Autonomous business-answer boundary

The generic mewcode Java Agent contains no RetailVision keyword router,
Product Analytics tool-name mapping, trusted-scope parser, product alias
defaults, or deterministic Product Analytics answer template. The active flow
is:

```text
user question
  -> LLM autonomous MCP tool selection
  -> RetailVision MCP tool and structured result
  -> LLM final answer
```

RetailVision facts and safety remain owned by the Python MCP instructions,
tool descriptions, `ProductAnalyticsMCPTools`, and
`ProductAnalyticsQueryService`. The grounding contract exposes public
anonymous facts and safe derived interpretations while excluding internal
scores, reason codes, booleans, internal keys, unsupported ratios, people
counts, and purchase conclusions.

The three-batch runner observes autonomous behavior with exactly one attempt
per question and no retries:

```powershell
python scripts/run_store_intelligence_business_answer_stability.py `
  --java-repo "D:\path\to\mewcode-java" `
  --provider-config "D:\path\to\mewcode-java\.mewcode\config.yaml" `
  --output-dir "reports\r5_03_2_autonomous_mcp_baseline_20260826_final" `
  --batches 3
```

It is observation evidence, not a Java routing or answer-enforcement
mechanism. The current autonomous observation is `19/21` (batches `7/7`,
`7/7`, `5/7`) with zero retries. R5-03.2.1 and its 21/21 deterministic evidence
are rejected/superseded architectural history and are not current production
qualification evidence.

R5-04 supersedes that observation with provider-level diagnostics and a generic
OpenAI-compatible stream lifecycle fix. Usage-only streaming chunks no longer
end Agent consumption before pending tool calls are flushed. Structured Print
and Remote events expose only sanitized counts, finish reason, answer presence,
and failure classification; they never expose prompts, credentials, raw
Provider configuration, or MCP child environment values.

The final R5-04 evidence is
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_04_autonomous_mcp_canary_20260826_final3\`: batches `7/7`, `6/7`, and
`7/7`, or `20/21`, with 21 invocations, 27 successful MCP calls, zero retries,
zero forbidden-field failures, zero missed tools, and one retained grounding
failure. This supports bounded internal Canary operation, not production
enablement.

R5-04.1 supersedes the semantic qualification state, without rewriting the
R5-04 evidence. Its final evidence is
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_04_1_autonomous_mcp_semantic_integrity_20260826_final8\`: batches
`7/7`, `6/7`, and `4/7`, or a recorded `17/21`, with `21/21` protocol,
`19/21` grounding, `19/21` presentation, 26 MCP calls, 21 invocations, zero
retries, zero stream/argument failures, and zero unknown product keys. One
batch 2 grounding failure is a checker false positive fixed after the run;
three batch 3 answer failures remain real. Production enablement is blocked.

R5-03.2 manual business-answer smoke runs the seven specified questions once
per isolated fixture and never retries a failed scenario:

```powershell
python scripts/run_store_intelligence_business_answer_smoke.py `
  --java-repo "D:\path\to\mewcode-java" `
  --provider-config "D:\path\to\mewcode-java\.mewcode\config.yaml" `
  --output-dir "reports\r5_03_2_business_answer_smoke_20260825"
```

`RETAILVISION_ACCEPTANCE_REQUIRED` is strict: only exact lowercase `true` or
`false` is accepted; an invalid value fails acceptance instead of falling back
to a skippable focused test. After a successful connection, a disabled server
returns the stable Java result `product_analytics_mcp_disabled:<safe-server>`
for `tools/call`; SDK exception text and local deployment details remain out of
Agent context.
