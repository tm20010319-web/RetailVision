# REFACTOR-R4-05 Documentation Authority Audit

Audit date: 2026-08-24

## Scope and Snapshot

This is an audit-only deliverable. No existing Markdown, source, test,
database, schema, media, or evidence file was moved, deleted, renamed, merged,
or rewritten.

The inventory below is the pre-deliverable physical snapshot: **191
Markdown files** discovered outside `.git/` before this Registry and report
were written. The two deliverables are intentionally excluded from that
baseline; the post-write physical count is **193**. The snapshot
includes untracked generated Stage10 and graphify Markdown. Tracked status is
the status at audit time, not an authorization decision.

## Authority Map

| Area | Current routed authority |
| --- | --- |
| First-read documentation routing | [`docs/authority/DOCUMENT_AUTHORITY_REGISTRY.md`](../authority/DOCUMENT_AUTHORITY_REGISTRY.md), P0 routing-only |
| Business context | [`CONTEXT.md`](../../CONTEXT.md) and [current handoff](../handoff/RETAILVISION_CURRENT_HANDOFF.md) |
| Accepted decisions | [`docs/adr/`](../adr/) |
| Repository instructions | [`AGENTS.md`](../../AGENTS.md) and [`docs/agents/`](../agents/) |
| Frozen logical architecture | [`docs/architecture/v2/`](../architecture/v2/) |
| Approved target design | [`docs/architecture/v3/`](../architecture/v3/), phase-gated |
| Product Analytics/MCP runtime truth | Current production code/tests; MCP process `python -m agent.mcp_server.server` |
| Validation | Historical reports and verification evidence are stored in the external archive; current state is summarized by the handoff |
| Historical/research | Stored in the external archive under `historical-docs/` |

The Registry routes readers; it does not supersede any source in this table.

## Classification Counts

| Classification | Count |
| --- | ---: |
| CURRENT_AUTHORITY | 47 |
| CURRENT_DESIGN | 5 |
| VALIDATION_EVIDENCE | 115 |
| HISTORICAL | 19 |
| RESEARCH | 5 |
| DUPLICATE | 0 |
| UNKNOWN | 0 |

Conflict-status counts: `NOT_CONFLICTING` = 190; `CONFLICTING` = 0; `REVIEW_REQUIRED` = 1.

## Current Authoritative Architecture Review

- Production behavior and tests remain the implementation truth.
- Accepted ADRs and `CONTEXT.md` govern domain contracts.
- `docs/architecture/v2/` is the frozen logical architecture baseline.
- `docs/architecture/v3/` is approved target/refactor design only; execution
  remains phase-gated.
- Validation reports describe outcomes and evidence; they do not authorize
  implementation changes.

The production Store Intelligence path is:

```text
Stage9 normalized behavior facts
  -> ProductInteractionFactWriter
  -> product_interaction_facts
  -> ProductAnalyticsQueryService
  -> RetailVision MCP server (stdio)
  -> external mewcode-java MCP client/Agent
```

The Python runtime under `tools/harness/product_analytics/` and the manual
`demo/product_analytics_agent/` remain harness/demo paths. They are not the
production Agent entrypoint. The only valid RetailVision MCP process entrypoint
is `python -m agent.mcp_server.server`. Identity, Visit, and direct database
access by the external Agent remain outside the MCP boundary.

## Focused Conflict Review

### Agent architecture

The current production Agent is the external mewcode-java Agent using its MCP
client. Python Product Analytics runtime/tool-calling code is a harness/demo
implementation. Existing historical plans that mention Python/Ollama or
realtime composition are outside the active reading path and are not strict
conflicts.

### MCP

The server is the stdio process `python -m agent.mcp_server.server`. The
canonical implementation exposes seven current tools:
`rank_products`, `get_product_behavior_summary`,
`compare_product_behavior`, `recommend_hot_products`,
`analyze_product_trend`, `detect_product_anomaly`, and
`suggest_product_display`.

[`docs/product_analytics_mcp_server.md`](../product_analytics_mcp_server.md)
is classified `CURRENT_AUTHORITY` for its setup subject but is marked
`REVIEW_REQUIRED` because its tool catalogue is stale and should be checked
against the code and tests. It is not marked strict `CONFLICTING`: the active
reading-path and executable-guidance criteria were not both established.

### Product Analytics

The current query boundary is `ProductInteractionFact` persisted in
`product_interaction_facts`, queried by `ProductAnalyticsQueryService`,
then exposed through MCP. Stage9 normalized facts are upstream writer input;
raw Stage9 artifacts are not the MCP query source. Formal `BehaviorEvent`
publication and Identity are not Product Analytics V1 ingestion prerequisites.

### Offline pipeline

The current workflow is offline video analysis through Stage9 normalization.
Older realtime assumptions remain in historical/research material and generated
Stage10 evidence. They are retained as evidence/context and are not current
implementation instructions.

## Conflict Results

Strict `CONFLICTING` requires active-path exposure plus executable guidance
that contradicts current authority. **No document met both conditions in this
snapshot.** One document is `REVIEW_REQUIRED` as noted above. This is a
governance warning, not an authorization to rewrite that document.

## Merge Candidate Analysis

Merge candidates are classification outputs only. No content merge was
performed.

### INDEX_CONSOLIDATION (default)

| Source documents | Reason | Recommended action |
| --- | --- | --- |
| `docs/README.md`, `docs/repository/DOCUMENT_AUTHORITY_MATRIX.md`, Registry | These are routing/governance entrypoints with different scopes. | Keep all bodies; link them from the Registry and docs README. |
| `docs/repository/PHASE*_EXECUTION_REPORT.md` and `REFACTOR_*.md` | Execution reports are independently dated evidence. | Keep individually; provide index links rather than merging evidence. |
| Repeated generated Stage10 profile/preflight Markdown | Each file is run-specific provenance. | Keep as evidence; index by stage/run if later approved. |
| Current and archived handoffs | Current handoff and historical handoffs have different authority. | Keep separate; route current readers to the current handoff. |

### CONTENT_MERGE_AFTER_OWNER_REVIEW

| Source documents | Reason | Required gate |
| --- | --- | --- |
| `docs/product_analytics_mcp_server.md` and selected non-evidence Product Analytics guidance | Possible overlap in setup/tool description. | Owner review and code/test comparison; never merge validation evidence into authority. |
| Repository governance inventories with overlapping scope | Some inventories repeat cleanup/status metadata. | Owner review, preservation of dated snapshots, and explicit replacement links. |

No merge is authorized by this audit. In particular, do not merge validation
evidence into authority documents, historical handoffs into current state,
v1/v2/v3 architectures together, or reports into business contracts.

## Archive Candidate Analysis

No move is authorized. Candidate sets for a later owner-approved archive pass
are:

- Root historical planning: `门店摄像头AI分析项目一个月开发计划.md` and
  `docs/2026-07-27-three-week-behaviour-video-scope.md`.
- Historical/research: `docs/research/`, `docs/GREETING_DETECTION_V1_V2_DEMO_COMPARISON.md`,
  and `skill/skill.md` after owner review.
- Already archived material under `docs/archive/` remains in place.
- Generated Stage10/graphify Markdown should be handled by a separate artifact
  policy, not silently promoted to authority or deleted from this audit.

## Proposed Registry Content

The separate Registry created with this audit contains only:

1. P0 first-read routing meaning and non-override boundary.
2. Authority resolution order.
3. Primary classification definitions.
4. Strict conflict-status rule.
5. Domain routing table.
6. Explicit non-authority boundaries.
7. Link to this detailed inventory.

It intentionally does not copy business or architecture body content. Any future
content consolidation requires owner review and a separate approved task.

## Complete Markdown Inventory

The following table is the complete pre-deliverable snapshot. `active_reading_path`
is yes/no; `conflicting_reference` is the document or code location used for
comparison, not a claim that it is itself authoritative.

| path | title | size_bytes | tracked | generated | location | classification | conflict_status | active_reading_path | conflict_reason | conflicting_reference | replacement_authority |
| --- | --- | ---: | :---: | :---: | --- | --- | --- | :---: | --- | --- | --- |
| `门店摄像头AI分析项目一个月开发计划.md` | # 门店摄像头 AI 分析项目一个月开发计划 | 14931 | no | no | root | RESEARCH | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `AGENTS.md` | ## Agent skills | 881 | yes | no | root | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `camera_profile_demo/README.md` | # 真实摄像头顾客画像 Demo | 1283 | yes | no | camera_profile_demo | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `cleanup/manifests/phase0a_manifest.md` | # Cleanup Phase 0A Manifest | 158571 | no | yes | cleanup | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `CONTEXT.md` | # RetailVision | 15320 | yes | no | root | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `demo_greeting/README.md` | # Camera Demo Greeting | 2932 | yes | no | demo_greeting | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/2026-07-27-three-week-behaviour-video-scope.md` | # Three-week behaviour and evidence-video scope | 6267 | yes | no | docs | RESEARCH | NOT_CONFLICTING | no | Historical or research material is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/adr/0001-unified-recognition-intake.md` | # Route all device recognition through the durable inbox | 923 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0002-use-device-recordings-for-evidence-clips.md` | # Use device recordings as the source for evidence clips | 835 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0003-mount-display-cameras-for-behaviour-observation.md` | # Mount display cameras for behaviour observation | 706 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0004-retain-seven-days-of-device-recordings.md` | # Retain seven days of continuous device recordings | 818 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0005-single-member-association-resolver.md` | # Use one resolver for member association and arrival facts | 2119 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0006-treat-association-outcome-as-rebuildable-projection.md` | # Treat association outcome as a rebuildable projection | 715 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0007-separate-finalization-workflow-from-binding-application.md` | # Separate Finalization Workflow From Binding Application | 910 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0008-separate-spatial-association-clock-from-business-event-clock.md` | # Separate Spatial Association Clock From Business Event Clock | 801 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0009-represent-track-identity-conflicts-with-terminated-bindings.md` | # Represent Track Identity Conflicts With Terminated Bindings | 849 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0010-follow-exact-track-lifecycle-for-confirmed-bindings.md` | # Follow Exact Track Lifecycle for Confirmed Bindings | 1746 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0011-project-durable-binding-provenance-onto-behaviour-events.md` | # Project Durable Binding Provenance onto Behaviour Events | 628 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0012-recover-durable-bindings-by-exact-application-identity.md` | # Recover Durable Bindings by Exact Application Identity | 709 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0013-treat-compatible-track-reconfirmation-as-no-op-application.md` | # Treat Compatible Track Reconfirmation as a No-op Application | 582 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0014-project-track-identity-through-one-exact-runtime-cache.md` | # Project Track Identity Through One Exact Runtime Cache | 625 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0015-fence-unterminated-confirmed-bindings-by-exact-track-token.md` | # Fence Unterminated Confirmed Bindings by Exact Track Token | 691 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0016-fail-stop-store-runtime-on-persistent-finalization-invariants.md` | # Fail-stop Store Runtime on Persistent Finalization Invariants | 732 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0017-project-finalization-terminal-outcomes-onto-recognition-inbox.md` | # Project Finalization Terminal Outcomes onto Recognition Inbox | 722 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0018-compose-finalization-and-binding-heartbeat-under-one-capability-gate.md` | # Compose Finalization and Binding Heartbeat Under One Capability Gate | 835 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0019-refresh-canonical-visit-from-confirmed-track-activity.md` | # Refresh Canonical Visit Activity From Confirmed Exact Tracks | 1658 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/adr/0020-converge-exact-anonymous-entrance-visits-before-finalization.md` | # Converge exact anonymous entrance visits before finalization | 1806 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/agents/domain.md` | # Domain Docs | 814 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/agents/issue-tracker.md` | # Issue tracker: GitHub | 1690 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/agents/triage-labels.md` | # Triage Labels | 820 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/DEVELOPMENT_GUIDE_V2.md` | # Development Guide v2 | 4330 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/FACT_OWNERSHIP_V2.md` | # Fact Ownership v2 | 5970 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/MODULE_DEPENDENCY_RULES_V2.md` | # Module Dependency Rules v2 | 6963 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/MODULE_MAPPING_V2.md` | # Module Mapping v2 | 21442 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/MODULE_MIGRATION_PLAN_V2.md` | # Module Migration Plan v2 | 3208 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/PHASE_MODULE_MAPPING.md` | # Phase to Module Mapping | 3850 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/PROJECT_MODULE_ARCHITECTURE_V2.md` | # RetailVision Project Module Architecture v2 | 9086 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/README.md` | # RetailVision Target Architecture v2 | 2255 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v2/TEST_ARCHITECTURE_V2.md` | # Test Architecture v2 | 4383 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v3/MODULE_MAPPING_V3.md` | # Module Mapping v3 | 8597 | no | no | docs | CURRENT_DESIGN | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v3/MODULE_MIGRATION_PLAN_V3.md` | # Module Migration Plan v3 | 13303 | no | no | docs | CURRENT_DESIGN | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/architecture/v3/TARGET_MODULE_ARCHITECTURE_V3.md` | # Target Module Architecture v3 | 10706 | no | no | docs | CURRENT_DESIGN | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/archive/architecture-v1/AGENT_MODULE_MAPPING.md` | # Agent Module Mapping - Target Architecture v1 - FROZEN | 13692 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/architecture-v1/DEVELOPMENT_GUIDE.md` | # Development Guide - Target Architecture v1 - FROZEN | 7317 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/architecture-v1/MODULE_DEPENDENCY_RULES.md` | # Module Dependency Rules - Target Architecture v1 - FROZEN | 10342 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/architecture-v1/MODULE_INVENTORY.md` | # Module Inventory | 6805 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/architecture-v1/MODULE_MIGRATION_PLAN.md` | # Module Migration Plan - Target Architecture v1 - FROZEN | 7872 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/architecture-v1/PROJECT_MODULE_ARCHITECTURE.md` | # Project Module Architecture - Target Architecture v1 - FROZEN | 19878 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/handoff/PROJECT_HANDOFF_20260805.md` | # RetailVision 项目完整交接文档 | 23235 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/handoff/SESSION_HANDOFF.md` | # RetailVision Session Handoff | 23376 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/handoff/STAGE10_TO_STAGE11_HANDOFF.md` | # STAGE10_TO_STAGE11_HANDOFF | 11819 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_1_LIVE_PROVIDER_VALIDATION_REPORT.md` | # Stage 10.1 Live Provider Validation | 3518 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_2_1_PRODUCT_LATENCY_REPORT.md` | # Stage 10.2.1 Product SAM Latency Decomposition & Safe Optimization | 6603 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_2_2_PERSON_PRESENT_REPORT.md` | # Stage 10.2.2 Person-Present Critical Path & Runtime READY Closure | 6968 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic & Preflight | 2525 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_2_PROFILE_ANALYSIS.md` | # Stage 10.2 / 10.2.1 Profile Analysis | 3762 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_2_REALTIME_ACCEPTANCE_REPORT.md` | # Stage 10.2 Realtime Acceptance & Latency Closure | 5229 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_3_LIVE_BEHAVIOR_ACCEPTANCE_REPORT.md` | # Stage 10.3 真实行为验收与中文 Demo Overlay 报告 | 7059 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_DEMO_INTEGRATION_REPORT.md` | # Stage 10 Demo Integration Report | 10282 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/archive/validation/stage10/STAGE10_PERFORMANCE_BLOCKER.md` | # Stage 10 Performance Blocker | 2214 | no | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/GREETING_DETECTION_V1_V2_DEMO_COMPARISON.md` | # 迎宾检测版本对比 | 15012 | yes | no | docs | RESEARCH | NOT_CONFLICTING | no | Historical or research material is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/handoff/PHASE2D_SAM3_HANDOFF_20260810.md` | # Phase 2D + SAM3 新会话交接 | 25049 | yes | no | docs | HISTORICAL | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/handoff/RETAILVISION_CURRENT_HANDOFF.md` | # RetailVision Current Handoff | 18893 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/offline_v1_module_mapping.md` | # Offline Behavior + Identity Foundation V1 Module Mapping | 1930 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/product_analytics_mcp_server.md` | # RetailVision Product Analytics MCP Server | 1558 | no | no | docs | CURRENT_AUTHORITY | REVIEW_REQUIRED | no | Tool catalogue lists only an older three-tool subset. | agent/product_interaction_mcp.py; docs/repository/ARCHITECTURE_CONSISTENCY_REVIEW.md | agent/mcp_server/server.py + canonical MCP schemas/tests |
| `docs/README.md` | # RetailVision Documentation | 3457 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/offline/OFFLINE_V1_COMPLETE_LOOP_GOLDEN_REPORT.md` | # Offline V1 Complete-loop Golden Report | 5322 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/offline/OFFLINE_V1_FINAL_FREEZE_REPORT.md` | # Offline V1 Final Freeze Report | 4591 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/offline/OFFLINE_V1_POST_FIX_TEST_REPORT.md` | # Offline V1 Post-Fix Test Report | 6643 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/offline/OFFLINE_V1_REVIEW_FIX_REPORT.md` | # Offline V1 Review Fix Report | 6509 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_CAPABILITY_PROFILE_REAL_DEVICE_VALIDATION.md` | # P6S Capability Profile Real-device Validation | 7738 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_DEVICE_FINGERPRINT_REAL_DEVICE_VALIDATION.md` | # P6S Device Fingerprint Real-device Validation | 7252 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_DURABLE_PUSH_COVERAGE_VALIDATION.md` | # P6S Durable Push Coverage Foundation Validation | 6956 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_DURABLE_PUSH_INBOX_PROVIDER_VALIDATION.md` | # P6S Durable Push Inbox Recognition Provider Validation | 5591 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_EVENT_804_SPATIAL_IDENTITY_DIAGNOSTIC.md` | # P6S Event 804 Spatial Identity Diagnostic | 4851 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_FACE_RECO_PUSH_REAL_DEVICE_VALIDATION.md` | # P6S FaceReco Push Real-device Validation | 13474 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_FACE_RECORDING_SPATIAL_GEOMETRY_VALIDATION.md` | # P6S Face Recording Spatial Geometry Validation | 4301 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_GUARDED_INTERIOR_EVENT_TIME_VALIDATION.md` | # P6S Guarded Interior Event-Time Validation | 5330 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_OFFLINE_TRACK_IDENTITY_REAL_DEVICE_VALIDATION.md` | # P6S Offline Track Identity Real-device Validation | 6648 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_PAGINATION_REAL_DEVICE_VALIDATION.md` | # P6S Recognition Pagination Real-device Validation | 3897 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_RECORDING_BOUNDARY_EVENT_VALIDATION.md` | # P6S Recording Boundary Event Validation | 7490 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_RECORDING_SEGMENT_BOUNDARY_VALIDATION.md` | # P6S Recording Segment Boundary Validation | 4976 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_RECORDING_TIME_REAL_DEVICE_VALIDATION.md` | # P6S Recording Time Real-device Validation | 7415 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_RECORDINGFILE_REAL_DEVICE_VALIDATION.md` | # P6S RecordingFile Real-device Validation | 4867 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/reports/p6s/P6S_TRUSTED_PRODUCTION_PUBLICATION_VALIDATION.md` | # P6S Trusted Production Publication Validation | 10691 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/ARCHITECTURE_CONSISTENCY_REVIEW.md` | # RetailVision Architecture Consistency Review | 15422 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/CLEANUP_INVENTORY.md` | # RetailVision Cleanup Inventory | 12049 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/DATABASE_AND_DATA_ASSET_INVENTORY.md` | # Database And Data Asset Inventory | 10643 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/DOCUMENT_AUTHORITY_MATRIX.md` | # Document Authority Matrix | 11502 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE0A_EXECUTION_REPORT.md` | # Phase 0A Execution Report | 4631 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1A_DOCUMENT_MOVE_MANIFEST.md` | # Phase 1A Document Move Manifest | 3715 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1A_EXECUTION_REPORT.md` | # Phase 1A Documentation Cleanup Execution Report | 4755 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1B_REPORT_MOVE_MANIFEST.md` | # Phase 1B Validation Report Move Manifest | 16574 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1B1_EXECUTION_REPORT.md` | # Phase 1B.1 Current Validation Report Migration | 4909 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1B2_EXECUTION_REPORT.md` | # Phase 1B.2 Stage10 Historical Validation Archive | 5915 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1C1_DOCUMENT_MOVE_MANIFEST.md` | # Phase 1C.1 Document Move Manifest | 4558 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1C1_EXECUTION_REPORT.md` | # Phase 1C.1 Historical Handoff, Verification and Governance Consolidation | 4065 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1C2_DOCUMENT_MOVE_MANIFEST.md` | # Phase 1C.2 Current Handoff Transition Manifest | 2441 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE1C2_EXECUTION_REPORT.md` | # Phase 1C.2 Current Handoff Transition | 3560 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE2A_HARNESS_ISOLATION_REPORT.md` | # Phase 2A Product Analytics Harness Isolation Report | 4774 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/PHASE2A_HARNESS_MOVE_MANIFEST.md` | # Phase 2A Product Analytics Harness Move Manifest | 2308 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R2B_DEMO_TOOLS_INVENTORY.md` | # REFACTOR-R2B Demo / Tools Boundary Inventory | 12514 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R2C01_REPORT.md` | # REFACTOR-R2C-01 Small Training Tool Migration Report | 2633 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R2C02_STAFF_VEST_MIGRATION_MANIFEST.md` | # REFACTOR-R2C-02 Staff Vest Training Tool Migration Manifest | 2879 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R2C02_STAFF_VEST_MIGRATION_REPORT.md` | # REFACTOR-R2C-02 Staff Vest Training Tool Migration Report | 4496 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R3_01_5_AGENT_CAPABILITY_REPORT.md` | # REFACTOR-R3-01.5 Product Analytics Agent Capability Report | 3882 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R3_01_MCP_AGENT_INTEGRATION_REPORT.md` | # REFACTOR-R3-01: mewcode-java MCP Client Integration Validation | 10422 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R3_PREP01_ANALYTICS_MCP_AUDIT.md` | # REFACTOR-R3-PREP-01: Product Analytics and MCP Audit | 11589 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R4_01_CLEANUP_AUDIT.md` | # RetailVision REFACTOR-R4-01 Cleanup Audit | 19145 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R4_02A_EXECUTION_REPORT.md` | # RetailVision REFACTOR-R4-02A Execution Report | 4296 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R4_02B_OUTPUT_STAGE_AUDIT.md` | # RetailVision REFACTOR-R4-02B Output and Stage10 Artifact Audit | 10167 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R4_02C_MANIFEST_REPORT.md` | # RetailVision REFACTOR-R4-02C Artifact Manifest Report | 4355 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R4_03A_UNKNOWN_CLASSIFICATION.md` | # RetailVision REFACTOR-R4-03A UNKNOWN Artifact Classification Audit | 79054 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R4_03B_CLEANUP_REPORT.md` | # RetailVision REFACTOR-R4-03B Cleanup Report | 2041 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REFACTOR_R4_04A_TARGET_ARCHITECTURE.md` | # RetailVision R4-04A Target Architecture Plan | 14280 | no | no | docs | CURRENT_DESIGN | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/REPOSITORY_CLEANUP_PLAN.md` | # Repository Cleanup Execution Plan | 7363 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/RUNTIME_ARTIFACT_AUDIT.md` | # Runtime Artifact Audit | 5739 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/STAGE_FREEZE_CHECKPOINT_PLAN.md` | # Stage Freeze Checkpoint Plan | 6788 | no | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/STORE_AGENT_R1_REPORT.md` | # RetailVision STORE_AGENT_R1 Report | 3728 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/repository/TIMESTAMP_CONTRACT_NOTE.md` | # Product Analytics Timestamp Contract Note | 888 | no | no | docs | CURRENT_DESIGN | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/research/sam3-official-20260810.md` | # SAM 3 Official Research (2026-08-10) | 13203 | no | no | docs | RESEARCH | NOT_CONFLICTING | no | Historical or research material is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `docs/STAFF_VEST_ANNOTATION_GUIDE.md` | # Staff vest annotation guide | 3916 | yes | no | docs | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/verification/PHASE_2A_BASELINE.md` | # Phase 2A Baseline | 9076 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/verification/PRE_CLEANUP_BASELINE.md` | # Pre-Cleanup Regression Baseline | 4391 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `docs/verification/TEST_INVENTORY.md` | # Test Inventory | 18048 | no | no | docs | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `graphify-out/GRAPH_REPORT.md` | # Graph Report - D:/GJJ/work/code  (2026-08-04) | 30656 | no | yes | graphify-out | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `reports/staff_vest_baseline_v1.md` | # Staff vest baseline v1 | 4159 | yes | no | reports | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `reports/staff_vest_test_blue_staff.md` | # Staff vest independent video test: test_blue_staff | 2436 | yes | no | reports | VALIDATION_EVIDENCE | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |
| `skill/skill.md` | # Store Vision Agent Refactor | 2548 | yes | no | skill | RESEARCH | NOT_CONFLICTING | no | Historical or research material is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_after_warmup/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_2_1_profile_after_warmup | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_after_warmup/stage10_2_performance/performance_before_after.md` | # Stage 10.2.1 Performance Before/After | 448 | no | yes | stage10_2_1_profile_after_warmup | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_after_warmup/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_1_profile_after_warmup | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_before_sam3/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 988 | no | yes | stage10_2_1_profile_before_sam3 | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_before_sam3/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_1_profile_before_sam3 | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_before_sam3/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_1_profile_before_sam3 | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_before/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_1_profile_before | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_1_profile_before/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1213 | no | yes | stage10_2_1_profile_before | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `STAGE10_2_1_REVIEW_MANIFEST.md` | # Stage 10.2.1 Review Bundle Manifest | 1663 | no | yes | root | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_empty_reference/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 988 | no | yes | stage10_2_2_empty_reference | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_empty_reference/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_2_empty_reference | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_empty_reference/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_2_empty_reference | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_bystander/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_2_2_person_present_bystander | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_bystander/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_2_person_present_bystander | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_bystander/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_2_person_present_bystander | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_invalid_14_windows/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_2_2_person_present_invalid_14_windows | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_invalid_14_windows/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_2_person_present_invalid_14_windows | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_invalid_14_windows/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_2_person_present_invalid_14_windows | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_invalid_15_windows/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_2_2_person_present_invalid_15_windows | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_invalid_15_windows/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_2_person_present_invalid_15_windows | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present_invalid_15_windows/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_2_person_present_invalid_15_windows | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_2_2_person_present | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_2_person_present | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_2_person_present/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_2_person_present | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `STAGE10_2_2_REVIEW_MANIFEST.md` | # Stage 10.2.2 Review Bundle Manifest | 1710 | no | yes | root | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_before_auto/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 881 | no | yes | stage10_2_before_auto | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_before/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_before | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_baseline_afterdiag/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 896 | no | yes | stage10_2_product_baseline_afterdiag | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_baseline_final/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_product_baseline_final | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_baseline_final/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_product_baseline_final | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_baseline_retry/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 896 | no | yes | stage10_2_product_baseline_retry | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_baseline_retry2/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 896 | no | yes | stage10_2_product_baseline_retry2 | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_baseline_reused/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Reference | 842 | no | yes | stage10_2_product_baseline_reused | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_baseline_reused/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_2_product_baseline_reused | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_preflight_run/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_2_product_preflight_run | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_2_product_preflight_run/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1213 | no | yes | stage10_2_product_preflight_run | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/B_approach_no_pickup/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 988 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/B_approach_no_pickup/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/B_approach_no_pickup/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1212 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/C_smart_bear_pickup/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/C_smart_bear_pickup/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/C_smart_bear_pickup/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1213 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup_retry/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup_retry/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup_retry/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1213 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup_retry2/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup_retry2/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup_retry2/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1213 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 988 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/D_smart_elephant_pickup/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/E_occlusion_recovery/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 990 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/E_occlusion_recovery/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/E_occlusion_recovery/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/empty_scene_overlay_smoke/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 988 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/empty_scene_overlay_smoke/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/empty_scene_overlay_smoke/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1212 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/F_multi_person/stage10_2_1_product_profile/product_latency_breakdown.md` | # Product SAM Latency Breakdown | 988 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/F_multi_person/stage10_2_performance/performance_before_after.md` | # Stage 10.2 Performance Before/After | 111 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `stage10_3_live_acceptance/F_multi_person/stage10_2_product_preflight/STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md` | # Stage 10.2 Product Initialization Diagnostic Report | 1205 | no | yes | stage10_3_live_acceptance | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `STAGE10_3_REVIEW_MANIFEST.md` | # Stage 10.3 Review Bundle Manifest | 1637 | no | yes | root | VALIDATION_EVIDENCE | NOT_CONFLICTING | no | Historical or generated evidence is outside the active implementation reading path. | - | CONTEXT.md + accepted ADRs + current production code/tests |
| `test/README.md` | # Test Guide | 709 | no | no | test | CURRENT_AUTHORITY | NOT_CONFLICTING | yes | No executable guidance inconsistent with current authority was established. | - | CONTEXT.md + accepted ADRs + current production code/tests + docs/architecture/v2 |

## Recommended Execution Order

1. Adopt the Registry as the P0 navigation entry without copying domain prose.
2. When changing implementation, read production code/tests, ADRs,
   `CONTEXT.md`, current handoff, and Architecture v2 in the authority order.
3. Correct or annotate stale setup/catalogue documents only in a separately
   approved docs-only change.
4. Use index consolidation before any content merge.
5. Require owner review before moving, archiving, merging, or deleting any
   document or generated evidence.

## Final Verification

- Baseline Markdown files scanned: **191**.
- Post-write physical Markdown count expected: **193**.
- Classification counts are listed above and sum to the baseline.
- Strict conflicts: **0**.
- Review-required documents: **1**.
- Merge candidates identified: **4 INDEX_CONSOLIDATION groups** and **2
  CONTENT_MERGE_AFTER_OWNER_REVIEW groups**.
- Archive candidate groups identified: **3**.
- Execution status: **AUDIT_ONLY**. No existing document, source, test,
  database, schema, evidence, or media path was changed by this task.
