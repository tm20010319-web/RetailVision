# RetailVision Product Analytics MCP Server

## 独立经营数据 MCP（2026-10-27）

`python -m agent.mcp_server.business_server` 为门店经营100问提供独立只读入口；
实现位于 `agent/analytics/store_business/`。原商品互动和会员行为工具保留。
问题目录来自用户提供的100问表；目录只存问题及分类，答案由持久化经营统计计算。
目录的 `domain` 显式关联工具，题目位置和每类题数不决定路由；`question_id` 仍为稳定报表 ID。
第59题“顾客流失预警”归 `customers`，不按表格位置归 `feedback`；发现的工具schema是调用合同，
消费方不能缓存“每十题一个领域”或旧题目归属。题号及统计口径保持不变。
目前是用户知情的综合零售预置数据验收阶段，未连接真实POS、ERP、人事或摄像头。

2026-10-28 审查修复（已用于云端独立候选，正式入口未替换）：

- 云端候选问答发现未配置阈值时仍出现“最像压货”等变相分类。经营 MCP 将事实限制以
  `Public fact interpretation contract:` 单行前缀声明；Java 最终回答器单独保留这些可信说明，
  不导入工具路由文字。分类限制同时用于直接回答与最终合成：不借库龄分桶、查询区间或近义词
  自设高库存/新品标准。自然语言遵守情况须经真实模型复核，提示约束不等于确定性语义校验。
  最新候选仍有未配置新品标准时的近义分类和跨商品比较错误；已记录CURRENT_STATUS，语义发布验收未通过。

- `RETAILVISION_BUSINESS_HIGH_STOCK_DAYS` 与 `RETAILVISION_BUSINESS_NEW_PRODUCT_DAYS` 为可选正整数；
  默认空，不采用隐含90/30天标准。`classification.status=not_configured` 时返回事实明细，不能自行分类。
- 第20题 `rankings.refund_rate`、`rankings.exchange_rate` 各自按比例降序计算，独立报告返回范围；
  预算裁剪保留两榜各自第一行。第73题按坪效排序，第11题TOP10上限归报表定义所有。
- 在覆盖完整、明确 `absence_means_zero` 且该指标支持相应维度时，分组缺少交易记录可表示零。
  缺覆盖、缺快照和不支持的维度不能补零；有库存且零销量单列关注项，可售天数仍不可计算。
- 新seeder将临期库存按批次写入 `expiring_stock` 的 `batch_id`、`expires_on` 维度，合计数量与原规则一致。
  第28/99题的 `expiry_details` 提供查询期末基准、到期日和剩余天数。批次不完整或旧快照缺日期时
  返回unavailable，不从数量推断期限。不更改原数据库表结构，不自动回填或覆盖已有快照。
- Java混合目录保留全部真实schema；成功经营查询不会再被旧视觉关键词补偿覆盖。
  原视觉补偿仅用于没有成功经营查询的路径，仍是既有兼容逻辑，并未宣称全面移除关键词判断。

- 数据库包含数据集元信息、门店、实体、按日期及业务维度记录的统计分子/分母和覆盖记录。
  `synthetic_acceptance`、生成器指纹、批次及日期范围保留。普通回答不重复模式标签；
  来源/真实性被询问时如实说明。不得把预置数据写入原会员/视觉事实库。
- `RETAILVISION_BUSINESS_MCP_ENABLED` 默认false；启用必须提供 `RETAILVISION_BUSINESS_DATABASE_PATH`、
  `RETAILVISION_BUSINESS_ALLOWED_STORES` 和 `RETAILVISION_BUSINESS_DEFAULT_STORE`。
  SQLite 用 `mode=ro` 与 `query_only=ON`，启动与查询不迁移、不创建、不写库。
- `query_business_customers/sales/inventory/finance/staff/feedback/marketing/operations/market/procurement`
  共10个工具覆盖1–100题；本模块负责参数、门店白名单、日期、统计口径及错误代码。
  Java 使用实际发现的schema，不复制100题路由或预写答案。额外参数和任意SQL均拒绝。
- 日期是门店 `Asia/Shanghai` 日历日，查询末日包含在内，最长366天。
  月度同比/环比按同日进度比较；完整月份比较完整月份。数据覆盖不足返回unavailable。
- 销售与利润使用分为单位的金额统计，比例使用汇总后的分子/分母；库存取期末快照、期初库存取期初，
  不把多日库存相加。会员和活动人数按标识去重。零分母不当零。
- 查询结果含时间窗、截止日期、公开口径、对比、解释限制。`rows_limited` 表示明细只展示部分行，
  `returned_rows/total_rows` 明示范围；非空 `totals` 是所选门店与日期内的独立汇总，不由显示行外推。
  新品、低库存等筛选子集不提供 `totals`，避免用全店总量代替筛选结果。
  `truncated` 保留给 Java 长度预算造成的证据正文截断；两者语义不同。服务先减少明细行并保持完整JSON，
  不能去除真实截断标记或从已显示的部分行推算缺失事实。
- 增量营收、LTV、员工责任和未来收益等需要额外依据；当前数据可回答相关统计与受限建议，
  不因覆盖100个问题就声称验证了因果、预测或真实经营结论。

独立生成与回归入口：`python -m agent.analytics.store_business.seed --output <新文件> --end-date YYYY-MM-DD`；
`python -m unittest discover -s test -p test_store_business.py -v`。
真实模型/云端验收与确定性统计回归分别记录，不能把后者当成已上线证明。

会员initialize说明必须发布自己的时间窗。默认全店查询以省略 `camera_id` 表示；`store-wide` 是范围概念，不能作为相机ID传入，也不能沿用匿名商品工具的camera/time。未指定更窄范围时，recent会员工具可用空参数让服务应用可信默认值；用户明确给出的越界范围仍拒绝，不自动换成默认窗口。

The production boundary is:

```text
mewcode-java Agent
  -> mewcode MCP Client
  -> RetailVision MCP Server (stdio)
  -> ProductAnalyticsMCPTools -> ProductAnalyticsQueryService -> product_interaction_facts
  -> MemberBehaviorMCPTools -> MemberBehaviorQueryService -> members + behavior_events
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
$env:RETAILVISION_MEMBER_BEHAVIOR_MCP_ENABLED = "true"
$env:RETAILVISION_MEMBER_DATABASE_URL = "sqlite:///D:/GJJ/work/code/runtime/databases/demo_flow.db"
$env:RETAILVISION_MEMBER_STORE_ID = "store-1"
$env:RETAILVISION_MEMBER_CAMERA_ID = "" # blank means all cameras in the trusted store
$env:RETAILVISION_MEMBER_ANALYSIS_START_AT = "2026-09-01T00:00:00"
$env:RETAILVISION_MEMBER_ANALYSIS_END_AT = "2026-09-11T00:00:00"
$env:RETAILVISION_MEMBER_TIMEZONE = "Asia/Shanghai"
python -m agent.mcp_server.server --database-url "sqlite:///D:/GJJ/work/code/runtime/databases/retailvision.db"
```

`RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED` is `false` by default. Invalid
boolean values fail startup. When disabled, `initialize` remains available but
`tools/list` and `tools/call` return the stable JSON-RPC error
`product_analytics_mcp_disabled`.

Disabled composition does not construct a database engine, inspect schema, or
create/modify a database file. This makes the MCP flag a database-safe kill
switch as well as a tool-discovery switch.

The integration uses MCP stdio; the historical mewcode-java integration provides
`StdioClientTransport`; protocol messages are newline-delimited JSON-RPC on
stdin/stdout and diagnostics go to stderr.

The database configuration is resolved in this order:

1. `--database-url` command-line argument
2. `RETAILVISION_DATABASE_URL`
3. `DATABASE_URL`
4. local fallback `sqlite:///runtime/databases/retailvision.db`

Member Behavior independently resolves `--member-database-url`, then
`RETAILVISION_MEMBER_DATABASE_URL`, and finally the Product Analytics database
for backwards compatibility. Its preferred scope variables are
`RETAILVISION_MEMBER_STORE_ID`, `RETAILVISION_MEMBER_CAMERA_ID`,
`RETAILVISION_MEMBER_ANALYSIS_START_AT`,
`RETAILVISION_MEMBER_ANALYSIS_END_AT`, and `RETAILVISION_MEMBER_TIMEZONE`.
When none of the member scope variables are configured, the legacy unprefixed
scope remains supported. This permits Product Analytics to keep its cam02
window while Member Behavior reads a separate store-wide database.

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
      RETAILVISION_MEMBER_BEHAVIOR_MCP_ENABLED: "true"
      RETAILVISION_MEMBER_DATABASE_URL: ${RETAILVISION_MEMBER_DATABASE_URL}
      RETAILVISION_MEMBER_STORE_ID: "store-1"
      RETAILVISION_MEMBER_CAMERA_ID: ""
      RETAILVISION_MEMBER_ANALYSIS_START_AT: "2026-09-01T00:00:00"
      RETAILVISION_MEMBER_ANALYSIS_END_AT: "2026-09-11T00:00:00"
      RETAILVISION_MEMBER_TIMEZONE: "Asia/Shanghai"
```

Use a deployment-owned window appropriate for the canary; the values above
are historical sample scopes, not global defaults or proof of formal Event 804 publication.
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

`agent/analytics/product_catalog.py` is the single product-key resolver used by the MCP
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
- `get_member_behavior_summary`: summarize one named active member's confirmed
  public product interactions.
- `list_member_behavior_timeline`: return one member's chronological public
  pickup, holding, and put-down timeline.
- `list_recent_member_behaviors`: require no member reference and return the
  recent public timeline plus overall, per-member, and per-product aggregates;
  `limit` defaults to 100 and supports 1 through 200.

The two display tools are deliberately distinct: "推荐重点陈列哪些商品" routes
to `recommend_hot_products`; "商品应该怎样调整陈列" routes to
`suggest_product_display`.

仅有视觉数据源时，“完整报告”“综合门店报告”或同义请求是组合查询，不是单一 Product
Analytics 查询。Store Agent 必须在同一个请求中取得 `rank_products` 和
`list_recent_member_behaviors`，分别呈现匿名商品整体互动与已确认会员行为。
两者可以使用不同的可信时间窗和摄像头范围，报告必须分别标注，不能把匿名
商品指标归到某位会员名下。

另启用经营数据源后，财务、库存和综合经营报告使用所需经营工具；明确的视觉互动报告仍分别标注匿名及会员来源范围。

## Tool schemas

The server's `tools/list` response is projected from
`ProductAnalyticsMCPTools.list_tools()`. Each entry contains the tool name,
description, and a JSON Schema `inputSchema`; the server does not maintain a
second schema catalogue.

All schemas reject unknown properties. Product timestamp fields use naive ISO-8601
local time, without a timezone suffix. Product `limit` fields accept integers from 1
through 100 and default to 10 where present; `list_recent_member_behaviors` defaults
to 100 and accepts 1 through 200. Query windows may not exceed 31 days. The configured `StoreContext` rejects an explicit foreign store and, when
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
  --output-dir "D:\path\to\new-acceptance-output" `
  --test-summary-source "D:\path\to\verified-test-summary.json"
```

Provider credentials and temporary database configuration are used only in a
temporary config and are never written to the evidence JSONL/manifest.
The runner fails closed when an answer introduces unsupported commerce,
identity/Visit, intent, interaction-quality, or physical-placement claims;
reports a number or structured code absent from tool results; or contradicts a
structured recommendation. If forbidden data is detected, stored arguments
are cleared and the answer is replaced with a fixed redaction marker.

Historical acceptance directories, scores and checker revisions are retained byte-for-byte
outside the repository; see [archive index](repository/ARCHIVE_INDEX.md).
The acceptance runner refuses non-empty output directories and verifies its summary/manifest;
do not overwrite or relabel old results. R5-04.2 remains ACCEPTANCE_BLOCKED, as summarized
in [current status](CURRENT_STATUS.md). Historical Java behavior below is an integration
contract/reference; this documentation task did not inspect or qualify an external checkout.

## Agent grounding contract

`agent/mcp_server/grounding_contract.py` separates the contract into four
layers: `FACT_LABELS`, `HARD_GROUNDING_BOUNDARY`,
`SAFE_DERIVED_INTERPRETATIONS`, and `GLOBAL_PRESENTATION_GUIDANCE`. The server
instructions publish all four layers in strict mode; each tool description
publishes only its own routing, supported conclusions, and specific limits.
With `STORE_STRICT_INFERENCE_BOUNDARY=false`, development mode may label
interaction interest/attention judgments and propose display, staff, or
campaign experiments when they follow returned facts. It must still separate
observations, analysis, and advice; it may not fabricate values or state that
pickup/holding/put-down proves a purchase, transaction, sale, revenue, or
conversion. The Agent should lead with a concise conclusion and only expand to
all public fields when explicitly requested.

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
  --output-dir "D:\path\to\new-stability-output" `
  --batches 3
```

It is observation evidence, not a Java routing or answer-enforcement mechanism.
Historical batch scores belong in the external archive. A passing Python tool boundary
does not by itself prove autonomous Agent answer acceptance or production readiness.

R5-03.2 manual business-answer smoke runs the seven specified questions once
per isolated fixture and never retries a failed scenario:

```powershell
python scripts/run_store_intelligence_business_answer_smoke.py `
  --java-repo "D:\path\to\mewcode-java" `
  --provider-config "D:\path\to\mewcode-java\.mewcode\config.yaml" `
  --output-dir "D:\path\to\new-smoke-output"
```

`RETAILVISION_ACCEPTANCE_REQUIRED` is strict: only exact lowercase `true` or
`false` is accepted; an invalid value fails acceptance instead of falling back
to a skippable focused test. After a successful connection, a disabled server
returns the stable Java result `product_analytics_mcp_disabled:<safe-server>`
for `tools/call`; SDK exception text and local deployment details remain out of
Agent context.

## Historical validation lessons retained as current reference

Member Behavior queries may compose related read-only tools when the response remains grounded in
their returned facts and respects each tool's scope. A controlled fixture's obsolete exact-single-tool
check is not the business contract. MB-01 run8's original BLOCKED result and its separate policy-review
PASS remain distinct historical evidence; neither proves real-member production acceptance.

R5-04.2 recovery batches contain different unresolved grounding, presentation and MCP-call failures.
Their original manifests/JSONL remain available locally; newer partial results do not silently close old failures.
The pre-recovery all-process-failed batch was traced to Windows JDK AF_UNIX socket initialization,
before Provider/MCP traffic. If that same `UnixDomainSockets` / `Invalid argument: connect` failure recurs,
inspect the JDK socket directory; historical recovery used an explicit `-Djdk.net.unixdomain.tmpdir`
outside the problematic Temp tree. Changing only `java.io.tmpdir` did not address that JDK path.
This is external deployment troubleshooting context, not a reason to alter RetailVision routing or credentials.
