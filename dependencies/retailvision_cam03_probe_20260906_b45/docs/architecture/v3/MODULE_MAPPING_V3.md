# Module Mapping v3

Status: `DESIGN_ONLY`. This mapping extends Architecture v2 with Product
Analytics and MCP. It does not move code.

## Production and integration mapping

| Existing | Target | Responsibility | Dependency rule | Migration disposition |
| --- | --- | --- | --- | --- |
| `pipelines/store_runtime.py` | `runtime/application` | composition, supervision | depends on capability ports | split later |
| `pipelines/behavior_runtime.py` | `runtime/application`, `behavior/application/runtime` | compose realtime behavior | runtime must not own behavior rules | split later |
| `pipelines/offline_behavior_pipeline.py` | `behavior/offline/application` | Offline V1 orchestration | analytics only via current writer port | keep until Phase 4 |
| `agent/offline_source.py`, `offline_media.py`, `offline_artifacts.py` | `behavior/offline` plus `evidence` | source/media/artifact gates | preserve authoritative PTS and artifact fence | keep |
| `agent/offline_publication.py`, `offline_publication_sql.py` | `behavior/offline/publication` | formal publication/fence | separate from anonymous analytics | keep |
| `agent/offline_identity*.py` | `identity/track_association` | historical identity association | confirmed-only member projection | keep |
| `agent/behavior_events.py` | `behavior/events` | BehaviorEvent creation/query ports | behavior owns fact; API uses service | split later |
| `agent/behavior_monitor.py`, `behavior_vision.py` | `behavior/dwell`, `entry`, `tracking/person` | realtime observations | no Visit/member decisions | split later |
| `agent/sam31_behavior_adapter.py`, `sam31_realtime_providers.py` | `behavior/tracking/person/providers` | SAM3 person/provider adapter | frozen sampling/window contract | keep |
| `agent/sam31_product_tracking.py` | `behavior/tracking/product` | product observations | no analytics persistence | keep |
| `agent/person_product_association.py` | `behavior/interaction/association` | person-product association | Stage8 provenance only | keep |
| `agent/product_interaction.py` | `behavior/interaction` | Stage9 session state machine | frozen PICK_UP/HOLDING/PUT_DOWN | keep |
| `agent/product_interaction_analytics.py` writer | `analytics/product/application/ingest` | current ProductInteractionFactWriter; scope/time validation and persistence | consumes normalized fact port; no separate admission service/fence | REFACTOR-R3 |
| `agent/product_interaction_analytics.py` query | `analytics/product/application/query` | aggregates product read model | no artifact/MCP dependency | REFACTOR-R3 |
| `agent/models.py:ProductInteractionFact` | Business: `analytics/product`; physical: `infrastructure/persistence/analytics` | SQLAlchemy ORM persistence model | not a formal BehaviorEvent; physical split is future-only | ORM split last |
| `agent/product_interaction_mcp.py` | `integration/mcp/product_analytics` | query service adapter | depends inward on analytics port | REFACTOR-R3 |
| `agent/mcp_server/*` | `integration/mcp/{protocol,transport,product_analytics}` | MCP protocol/server/transport | no domain or SQL decisions | REFACTOR-R3 |
| `tools/harness/product_analytics/*` | `tools/harness/product_analytics` | Python Tool Calling Harness | Harness only; `production_reachable = false`; may call public MCP interfaces in tests/harness | CURRENT PHYSICAL LOCATION (REFACTOR-R2A) |
| `demo/product_analytics_agent/run.py` | `demo/product_analytics_agent` | manual Product Analytics demo entrypoint | demo-only; never production imports it | CURRENT PHYSICAL LOCATION (REFACTOR-R2A) |
| `agent/product_analytics_agent_runtime.py`, `agent/product_analytics_tool_calling.py` | compatibility surface for `tools/harness/product_analytics` | temporary re-export shims | contains no Harness implementation; retain until post-REFACTOR-R3 stability review | TEMPORARY_COMPAT_SHIM |
| `agent/device_*`, `face_spatial_evidence.py`, `store_resolution.py` | `identity/recognition` | recognition intake and scope | no behavior tracking | Phase 5 |
| `agent/camera_track_identity.py`, policy/association files | `identity/track_association` | temporal/spatial evaluation | frozen Primary/status policy | Phase 5 |
| `agent/track_association_finalization.py` | `identity/binding` | durable binding/finalization | projects through ports only | Phase 5 |
| `agent/customer_entry_visit.py` | `visit/entry` | entry-backed Visit fact | behavior signals through a port | Phase 6 |
| `agent/reusable_visit_resolver.py` | `visit/resolver` | canonical Visit/Arrival | no detector logic | Phase 6 |
| `agent/visit_merge_service.py`, `visit_activity.py` | `visit/{merge,activity}` | merge/activity | preserve ADR 0019/0020 | Phase 6 |
| `agent/member_*` except arrival effects | `member` | Member and statistics | statistics from effective arrivals | Phase 6 |
| `agent/member_arrival_effects.py` | `effects` | side-effect lifecycle | consumes arrival fact | Phase 6 |
| `agent/device_recordings.py`, capture/evidence files | `evidence` | recordings/clips | does not classify behavior | Phase 6 |
| `agent/profile_*`, `customer_profile_analyzer.py` | `profile` | profiles/jobs | inbox is not arrival | Phase 6 |
| `agent/operations_query_service.py` | `operations/read_model` | diagnostics and queries | read-only | Phase 6 |
| `agent/router.py`, `member_api.py`, camera APIs | `api` | HTTP transport | depends on application ports | Phase 6 |
| `agent/db.py`, `schema_evolution/`, `config.py`, `models.py` | `infrastructure` | persistence/config adapters | ORM physical split last | Phase 7 |

## Demo, tools, legacy, and generated mapping

| Existing | Category | Target | Disposition |
| --- | --- | --- | --- |
| `demo_greeting/`, `demo-frontend/` | DEMO_SOURCE | `demo/greeting`, `frontend/demo` | R2B audit; move only after review |
| `camera_profile_demo/` | DEMO_SOURCE | `demo/camera_profile` or external assets | owner review first |
| Stage10 source helpers | DEV_TOOL/DEMO_SOURCE | `demo/stage10` or `tools/acceptance` | keep provenance; R2B audit |
| root run/smoke/test helpers | DEV_TOOL | `tools/{runtime,diagnostics,hardware}` | R2B audit; retain production roots |
| `tools/training/*` | TOOLS / TRAINING | `tools/training` | standalone Staff Vest operational tools; `production_reachable = false`; R2C-01/R2C-02 completed |
| legacy entry/identity/simple tracker paths | LEGACY | `legacy/` | only after import graph and gates |
| `outputs/`, `.task*`, `runs/`, `graphify-out/` | RUNTIME_GENERATED | `artifacts/{test-runs,profiling,graph}` | Phase 0, per-file manifest |
| `analysis_results/`, generated reports | VALIDATION_REPORT | `artifacts/reports` | protect authoritative/restricted evidence |
| root zip/patch/log/db | ARCHIVE_BUNDLE/RUNTIME_GENERATED | `artifacts/{reports,runtime}` or delete candidate | manual review |

## Test mapping

| Current test pattern | Target test area |
| --- | --- |
| behavior, SAM3, product tracking, association, interaction | `tests/behavior/` |
| device recognition, track association, binding, identity | `tests/identity/` |
| reusable Visit, merge, activity, entry | `tests/visit/` |
| member services/statistics/API domain tests | `tests/member/` |
| member arrival effects | `tests/effects/` |
| product interaction analytics | `tests/analytics/` |
| Product Analytics MCP protocol/server | `tests/mcp/` |
| offline source/media/artifact/publication/pipeline/B2.8 | `tests/offline/` |
| StoreRuntime and cross-capability tests | `tests/integration/` |
| P6S/manual camera/live model tests | `tests/hardware/` |
| Stage10 and greeting demonstrations | `tests/demo/` |
| explicitly unsupported compatibility tests | `tests/legacy/` |

Stage and Phase labels remain test markers or metadata, not folders at the
first level.

## Ownership and dependency summary

```text
Stage9 normalized behavior facts
  -> scope/time validation
  -> ProductInteractionFactWriter
  -> product_interaction_facts
  -> analytics/product query service
  -> integration/mcp adapter
  -> mewcode-java MCP client / external Agent Runtime
```

The reverse dependencies are forbidden: behavior must not import MCP,
analytics must not call Stage9 artifacts directly, MCP must not import ORM
models to implement business queries, and the Python harness must not become a
production Agent module.

## Current V1 boundary

Identity and formal `BehaviorEvent` publication do not participate in Product
Analytics ingest. An admission service, admission fence, or sealed-artifact
ingestion pipeline is not present in the current implementation. Such designs
belong under **Future Considerations**, not the V1 mapping.
