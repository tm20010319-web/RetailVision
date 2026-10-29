# Target Module Architecture v3

Status: `DESIGN_ONLY`. This is a logical target only. It does not authorize
package moves, ORM splitting, Schema changes, algorithm changes, or changes to
Behavior/Identity/Offline V1 frozen contracts.

## Business capability map

```text
WHO                          -> identity
WHAT                         -> behavior
WHICH VISIT                  -> visit
WHAT BUSINESS ACTION         -> effects
WHAT BUSINESS INSIGHT        -> analytics
HOW EXTERNAL AGENTS ACCESS   -> integration/mcp
```

The production Agent path is external and frozen:

```text
mewcode-java Agent
  -> mewcode LLM
  -> mewcode MCP Client
  -> RetailVision MCP Server
  -> ProductAnalyticsQueryService
  -> Database
```

`ProductAnalyticsAgentRuntime` and `OllamaRetailToolCallingModel` live in
`tools/harness/product_analytics/`; their manual demo entrypoint is
`demo/product_analytics_agent/run.py`. They are Python Tool Calling
Harness/Demo code, not a RetailVision Python production Agent module.
`mewcode-java` is an external Agent Runtime, not part of the RetailVision
Python Domain.

## Capability rules

| Capability | Owns | Must not own |
| --- | --- | --- |
| `runtime` | Store composition, lease, runtime instance, supervision, shutdown | Identity/behavior policy or fact rules |
| `identity` | Recognition intake, mappings, track association, durable bindings and projections | Person/product detection, Visit lifecycle, effects |
| `behavior` | Person/product observations, Stage5/7C/8/9 adapters, interaction state machine, BehaviorEvent preparation | Member identity decisions, Visit canonicalization, effects |
| `visit` | Entry facts, Visit lifecycle, resolver, convergence, merge, arrival and activity | Vision inference and binding mutation |
| `member` | Member entity, tier/priority, device sync, effective-arrival statistics | Visit or arrival decisions |
| `effects` | MemberArrivalEffect intent/claim/ack/fail lifecycle | Recognition acceptance or identity decisions |
| `analytics` | Product Interaction facts and rebuildable business read models/queries | Authoritative behavior/identity decisions |
| `evidence` | Recordings, clips and review provenance | Behavior classification or identity association |
| `profile` | Profile jobs/snapshots and model analysis | Treating inbox state as a visit fact |
| `operations` | Health, audit, diagnostics, metrics and read models | Business fact writes |
| `api` | HTTP/DTO/command/query transport adapters | ORM access and domain policy |
| `integration/mcp` | MCP protocol, transport and tool exposure | Direct Stage9/artifact scanning or domain decisions |
| `infrastructure` | SQLAlchemy, Schema evolution, config, filesystem/camera/model adapters | Business policy |
| `legacy`, `demo`, `tools` | Compatibility, demos and diagnostics | Production writers or import side effects |

## Target logical tree

```text
runtime/
  application/
  platform/
identity/
  recognition/
  track_association/
  binding/
behavior/
  application/runtime/
  tracking/person/
  tracking/product/
  interaction/
  role/
  entry/
  dwell/
  reach/
  events/
  offline/application/
visit/
  core/
  entry/
  resolver/
  convergence/
  merge/
  arrival/
  activity/
  projection/
member/
effects/
evidence/
profile/
analytics/
  product/
    domain/
    application/ingest/        current writer boundary; no separate admission service
    application/query/
    read_model/
    infrastructure/            logical analytics adapters; physical persistence is infrastructure-owned
integration/
  mcp/
    protocol/
    transport/
    product_analytics/
tools/
  harness/product_analytics/  Python tool-calling harness only
demo/
  product_analytics_agent/    optional demo entrypoint placement
operations/
api/
infrastructure/
legacy/
frontend/
```

Stage names (`Stage5`, `Stage7C`, `Stage8`, `Stage9`, `Stage10`) remain delivery
phase, algorithm-stage, test-provenance, or artifact labels. They are not
long-term top-level modules.

## Existing to target mapping

Existing physical paths remain unchanged.

| Existing path | Target logical location | Notes |
| --- | --- | --- |
| `agent/models.py` | `infrastructure/persistence/models` plus capability ownership | Keep whole until final phase |
| `agent/behavior_events.py` | `behavior/events` | Formal BehaviorEvent rules remain frozen |
| `agent/behavior_monitor.py` | `behavior/dwell`, `behavior/entry`, `behavior/events` | Split only after tests |
| `agent/behavior_vision.py` | `behavior/tracking/person` | Preserve detector semantics |
| `agent/person_product_association.py` | `behavior/interaction/association` | Stage8 is provenance, not package root |
| `agent/product_interaction.py` | `behavior/interaction` | PICK_UP/HOLDING/PUT_DOWN is frozen |
| `agent/*product_replay.py` | `behavior/interaction/replay` | Test/tool adapter |
| `agent/sam31_product_tracking.py` | `behavior/tracking/product` | Model adapter |
| `agent/sam31_behavior_adapter.py` | `behavior/tracking/person` | Preserve provider contract |
| `agent/sam31_realtime_providers.py` | `behavior/tracking/providers` | EOF terminal-window contract is frozen |
| `agent/offline_behavior.py` | `behavior/offline/application` | Offline V1 adapter |
| `agent/offline_identity*.py` | `identity/track_association` and `identity/recognition` | Confirmed-only projection |
| `pipelines/offline_behavior_pipeline.py` | `behavior/offline/application` | Writer dependency remains a narrow port |
| `pipelines/behavior_runtime.py` | `runtime/application` plus `behavior/application/runtime` | Composition and policy stay separate |
| `pipelines/store_runtime.py` | `runtime/application` | Composition root only |
| `agent/models.py:ProductInteractionFact` | Business: `analytics/product`; physical: `infrastructure/persistence/analytics` | Current SQLAlchemy ORM remains in `agent/models.py`; separate pure model/repository is future design only |
| `agent/product_interaction_analytics.py` | `analytics/product/application/ingest` and `application/query` | Current `ProductInteractionFactWriter` and `ProductAnalyticsQueryService`; no AnalyticsAdmissionService/Fence |
| `agent/product_interaction_mcp.py` | `integration/mcp/product_analytics` | Delegates to query service |
| `agent/mcp_server/schemas.py` | `integration/mcp/protocol` | MCP/JSON-RPC schema only |
| `agent/mcp_server/transport.py` | `integration/mcp/transport` | stdio boundary |
| `agent/mcp_server/tools.py` | `integration/mcp/product_analytics` | Dispatch/error translation |
| `agent/mcp_server/server.py` | `integration/mcp` | No SQL/business aggregation |
| `tools/harness/product_analytics/*` | `tools/harness/product_analytics` | Current physical Harness location; never production Agent ownership |
| `demo/product_analytics_agent/run.py` | `demo/product_analytics_agent` | Current physical manual Demo entrypoint |
| `agent/product_analytics_agent_runtime.py`, `agent/product_analytics_tool_calling.py` | compatibility surface for `tools/harness/product_analytics` | Temporary re-export shims only; no implementation |
| `agent/track_association_finalization.py` | `identity/binding` | Behavior projection via a port |
| `agent/camera_track_identity.py` | `identity/track_association` | Frozen statuses/spatial policy |
| `agent/reusable_visit_resolver.py` | `visit/resolver` | Preserve Visit/Arrival semantics |
| `agent/visit_merge_service.py` | `visit/merge` | Audit-preserving merge |
| `agent/customer_entry_visit.py` | `visit/entry` | Visit fact writer |
| `agent/member_arrival_effects.py` | `effects` | Intent lifecycle only |
| `agent/device_recordings.py`, evidence modules | `evidence` | Device recordings remain media source of truth |
| `agent/profile_jobs.py`, `agent/profile_snapshot_store.py` | `profile` | No inbox-to-visit inference |
| `agent/operations_query_service.py` | `operations/read_model` | Read-only operations surface |
| `agent/router.py`, `agent/member_api.py` | `api` | No ORM/business policy in routes |
| `agent/db.py`, `agent/schema_evolution/`, `agent/config.py` | `infrastructure` | Physical/Schema migration last |
| demos and camera profile | `demo` | Isolated from production imports |
| root runners and diagnostics | `tools` | Explicit development commands |

## ORM ownership mapping

This table is logical only; `agent/models.py` is not split now.

| ORM class/group | Target owner |
| --- | --- |
| `BehaviorEvent` | `behavior/events` |
| `ProductInteractionFact` | Business ownership: `analytics/product`; physical persistence: `infrastructure/persistence/analytics` |
| `TrackIdentityBinding` | `identity/binding` |
| `Visit`, `VisitEntryEvidence`, `MemberEntryEvent` | `visit` |
| `Member`, `MemberPreference` | `member` |
| `MemberArrival` | `visit/arrival` |
| `MemberArrivalEffect` | `effects` |
| `EvidenceClip` and recording rows | `evidence` |
| recognition inbox/cursor/mapping/registration rows | `identity/recognition` |
| track evaluations/finalization jobs | `identity/track_association` and `identity/binding` |
| profile jobs/snapshots | `profile` |
| offline publication/fence rows | `behavior/offline` |

## Test architecture target

```text
tests/
  behavior/
  identity/
  visit/
  member/
  effects/
  analytics/
  mcp/
  offline/
  integration/
  hardware/
  demo/
  legacy/
```

Current `test/` remains the executable baseline. Stage-numbered tests are
provenance and should map into capability folders during a separately approved
test migration.

## Non-negotiable boundaries

- Behavior can be anonymous; identity failure must not erase qualified product
  facts or inject a member.
- Current V1 is `Stage9 -> normalized behavior fact -> scope/time validation
  -> ProductInteractionFactWriter -> product_interaction_facts`. Identity and
  formal BehaviorEvent publication are not part of this ingest path.
- Product Analytics queries use `ProductAnalyticsQueryService`; neither an LLM
  nor an MCP handler reads raw Stage9 artifacts.
- MCP owns protocol/tool exposure, not aggregation or persistence.
- Formal BehaviorEvent publication remains separate from the current analytics
  writer path. Any future admission/fence or sealed-artifact ingestion belongs
  in **Future Considerations**, not in the V1 implementation truth.
- No Visit, Arrival, Coupon, EvidenceClip, live binding, Schema, threshold, or
  algorithm change is introduced by this design.

## Future Considerations

An explicitly approved future design may introduce a separate admission/fence,
sealed-artifact ingestion, or a pure `analytics/product/domain` model with an
`infrastructure/persistence/analytics` SQLAlchemy repository. None of these is
part of the current V1 implementation or this reconciliation.
