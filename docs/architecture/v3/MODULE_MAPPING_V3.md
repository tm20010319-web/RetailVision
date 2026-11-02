# Module Mapping v3

Current physical paths: [CURRENT_MODULE_LAYOUT](../CURRENT_MODULE_LAYOUT.md). Target columns and proposed tests/ subdirectories are future design, not existing directories. The old Harness shims and router are retired; source history is in the external archive.

Status: `DESIGN_ONLY`. This mapping extends Architecture v2 with Product
Analytics and MCP. It does not move code.

## Production and integration mapping

| Existing | Target | Responsibility | Dependency rule | Migration disposition |
| --- | --- | --- | --- | --- |
| `pipelines/store_runtime.py` | `runtime/application` | composition, supervision | depends on capability ports | split later |
| `pipelines/behavior_runtime.py` | `runtime/application`, `behavior/application/runtime` | compose realtime behavior | runtime must not own behavior rules | split later |
| `pipelines/offline_behavior_pipeline.py` | `behavior/offline/application` | Offline V1 orchestration | analytics only via current writer port | keep; interface split is future design |
| `agent/offline/offline_source.py`, `agent/offline/offline_media.py`, `agent/offline/offline_artifacts.py` | `behavior/offline` plus `evidence` | source/media/artifact gates | preserve authoritative PTS and artifact fence | keep |
| `agent/offline/offline_publication.py`, `agent/offline/offline_publication_sql.py` | `behavior/offline/publication` | formal publication/fence | separate from anonymous analytics | keep |
| `agent/offline/offline_identity*.py` | `identity/track_association` | historical identity association | confirmed-only member projection | keep |
| `agent/behavior/behavior_events.py` | `behavior/events` | BehaviorEvent creation/query ports | behavior owns fact; API uses service | split later |
| `agent/behavior/behavior_monitor.py`, `agent/behavior/behavior_vision.py` | `behavior/dwell`, `entry`, `tracking/person` | realtime observations | no Visit/member decisions | split later |
| `agent/behavior/sam31_behavior_adapter.py`, `agent/behavior/sam31_realtime_providers.py` | `behavior/tracking/person/providers` | SAM3 person/provider adapter | frozen sampling/window contract | keep |
| `agent/behavior/sam31_product_tracking.py` | `behavior/tracking/product` | product observations | no analytics persistence | keep |
| `agent/behavior/person_product_association.py` | `behavior/interaction/association` | person-product association | Stage8 provenance only | keep |
| `agent/behavior/product_interaction.py` | `behavior/interaction` | Stage9 session state machine | frozen PICK_UP/HOLDING/PUT_DOWN | keep |
| `agent/analytics/product_interaction_analytics.py` writer | `analytics/product/application/ingest` | current ProductInteractionFactWriter; scope/time validation and persistence | consumes normalized fact port; no separate admission service/fence | future interface review |
| `agent/analytics/product_interaction_analytics.py` query | `analytics/product/application/query` | aggregates product read model | no artifact/MCP dependency | future interface review |
| `agent/models.py:ProductInteractionFact` | Business: `analytics/product`; physical: `infrastructure/persistence/analytics` | SQLAlchemy ORM persistence model | not a formal BehaviorEvent; physical split is future-only | ORM split last |
| `agent/analytics/product_interaction_mcp.py` | `integration/mcp/product_analytics` | query service adapter | depends inward on analytics port | future interface review |
| `agent/mcp_server/*` | `integration/mcp/{protocol,transport,product_analytics}` | MCP protocol/server/transport | no domain or SQL decisions | future interface review |
| `tools/harness/product_analytics/*` | `tools/harness/product_analytics` | Python Tool Calling Harness | Harness only; `production_reachable = false`; may call public MCP interfaces in tests/harness | CURRENT PHYSICAL LOCATION |
| `demo/product_analytics_agent/run.py` | `demo/product_analytics_agent` | manual Product Analytics demo entrypoint | demo-only; never production imports it | CURRENT PHYSICAL LOCATION |
| `agent/identity/device_*`, `agent/identity/face_spatial_evidence.py`, `agent/identity/store_resolution.py` | `identity/recognition` | recognition intake and scope | no behavior tracking | future interface review |
| `agent/identity/camera_track_identity.py`, policy/association files | `identity/track_association` | temporal/spatial evaluation | frozen Primary/status policy | future interface review |
| `agent/identity/track_association_finalization.py` | `identity/binding` | durable binding/finalization | projects through ports only | future interface review |
| `agent/visits/customer_entry_visit.py` | `visit/entry` | entry-backed Visit fact | behavior signals through a port | future interface review |
| `agent/visits/reusable_visit_resolver.py` | `visit/resolver` | canonical Visit/Arrival | no detector logic | future interface review |
| `agent/visits/visit_merge_service.py`, `agent/visits/visit_activity.py` | `visit/{merge,activity}` | merge/activity | preserve ADR 0019/0020 | future interface review |
| `agent/member/member_*` except arrival effects | `member` | Member and statistics | statistics from effective arrivals | future interface review |
| `agent/visits/member_arrival_effects.py` | `effects` | side-effect lifecycle | consumes arrival fact | future interface review |
| `agent/evidence/device_recordings.py`, capture/evidence files | `evidence` | recordings/clips | does not classify behavior | future interface review |
| `agent/profile/profile_*`, `agent/profile/customer_profile_analyzer.py` | `profile` | profiles/jobs | inbox is not arrival | future interface review |
| `agent/api/operations_query_service.py` | `operations/read_model` | diagnostics and queries | read-only | future interface review |
| `agent/db.py`, `schema_evolution/`, `config.py`, `models.py` | `infrastructure` | persistence/config adapters | ORM physical split last | future interface review |

## Demo, tools, legacy, and generated mapping

| Existing | Category | Target | Disposition |
| --- | --- | --- | --- |
| `demo/greeting/`, `frontend/` | DEMO_SOURCE | `demo/greeting`, `frontend/demo` | current physical placement; deeper separation remains design |
| `demo/camera_profile/` | DEMO_SOURCE | `demo/camera_profile` or external assets | current physical placement |
| Stage10 source helpers | DEV_TOOL/DEMO_SOURCE | `demo/stage10` or `tools/acceptance` | shared runtime code remains active; review tool-only consumers separately |
| root run/smoke/test helpers | DEV_TOOL | `tools/{runtime,diagnostics,hardware}` | retain production roots; review explicit diagnostics individually |
| `tools/training/*` | TOOLS / TRAINING | `tools/training` | standalone Staff Vest operational tools; `production_reachable = false`; R2C-01/R2C-02 completed |
| legacy entry/identity/simple tracker paths | LEGACY | `legacy/` | only after import graph and gates |
| `outputs/`, `runtime/` | RUNTIME_GENERATED | `artifacts/{test-runs,profiling,graph}` | per-file review; preserve database-referenced assets |
| `reports/`, generated reports | VALIDATION_REPORT | `artifacts/reports` | protect authoritative/restricted evidence |
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
