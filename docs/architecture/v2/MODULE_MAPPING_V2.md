# Module Mapping v2

本表按当前实际行为分类，不按文件名推断。`Incoming` 是主要调用者；`Outgoing` 是
当前直接依赖的模块。物理位置保持不变。

## Runtime and pipelines

| Current File | Current Actual Responsibility | Owning Capability / Target Submodule | Layer | Class | Phase | Production Caller | Incoming | Outgoing | Fact Read | Fact Write | Phase2D | Future Action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `run_store_runtime.py` | new/legacy mode validation and top-level factory | Runtime/application composition | RUNTIME | CORE (mixed) | MULTI | operator | CLI | runtime, behavior, identity, legacy factory | config | none | NO | SPLIT_LATER |
| `run_behavior_runtime.py` | component/debug Behavior startup | Behavior/application runtime entry | RUNTIME | SUPPORT | BASE | debug operator | CLI/tests | behavior runtime, config | config | behavior facts through app | YES | MOVE_LATER |
| `run_device_identity.py` | component/debug Identity startup | Identity/application runtime entry | RUNTIME | SUPPORT (mixed) | BASE | debug operator | CLI/tests | identity runtime, legacy factory | config | identity/visit through app | NO | SPLIT_LATER |
| `pipelines/store_runtime.py` | Store dependency assembly, Lease, workers, fatal/shutdown supervision | Runtime/application + platform | RUNTIME | CORE | MULTI | `run_store_runtime` | root entry/tests | Identity, Behavior, Visit, Lease | lease/runtime state | runtime lease; starts fact writers | NO | SPLIT_LATER |
| `pipelines/behavior_runtime.py` | camera runtime, capture runner, snapshot/cache/activity/Visit Entry glue | Behavior/application/runtime + camera adapter | RUNTIME | CORE | MULTI | StoreRuntime | root/tests | Behavior, Identity projection, Visit Entry | Person observations/cache | Behavior/Entry through ports | YES | SPLIT_LATER |
| `pipelines/device_identity_runtime.py` | Inbox/poller/association lifecycle plus explicit Legacy branch | Identity/application/runtime | RUNTIME | CORE (mixed) | MULTI | StoreRuntime | root/tests | Recognition, Resolver, projection, Legacy | Inbox/health | via workers | NO | SPLIT_LATER |
| `pipelines/customer_profile_pipeline.py` | LLM profile workflow tied to legacy realtime type | Profile/application | APPLICATION | SUPPORT (mixed) | BASE | Legacy runtime/tools | legacy/profile tests | Profile, LLM, legacy pipeline | image/profile input | profile result/job | NO | SPLIT_LATER |
| `pipelines/historical_video_pipeline.py` | offline video analysis | Operations/tools analysis | APPLICATION | TOOL | BASE | CLI/demo | demo/tests | legacy detector, LLM | video | reports | NO | MOVE_LATER |
| `pipelines/member_business_runtime.py` | legacy Member Entry/Profile worker lifecycle | Legacy/member-business | RUNTIME | LEGACY | BASE | legacy mode | root/device runtime | MemberEntry, Profile | legacy events/jobs | legacy facts | NO | LEGACY_ISOLATE |
| `pipelines/realtime_entry_pipeline.py` | legacy SimpleTracker/member matching path | Legacy/realtime-entry | APPLICATION | LEGACY | BASE | legacy mode/demo | legacy runtime/tests | detector, legacy identity | frames/matches | callbacks only | NO | LEGACY_ISOLATE |
| `pipelines/__init__.py` | eager re-export of production/support/legacy pipelines | Legacy import compatibility | ADAPTER | LEGACY debt | BASE | any `pipelines` import | broad callers | every pipeline | none | none | NO | LEGACY_ISOLATE |

## Identity, Behavior, Visit, Runtime platform

| Current File | Current Actual Responsibility | Owning Capability / Target Submodule | Layer | Class | Phase | Production Caller | Incoming | Outgoing | Fact Read | Fact Write | Phase2D | Future Action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent/device_recognition_intake.py` | normalized recognition intake, dedupe, mapping wait | Identity/recognition/intake | APPLICATION | CORE | BASE/2A | receiver/poller | Identity adapters | Inbox, Member Mapping, Face Spatial | mapping/inbox | Inbox | NO | KEEP_CURRENT |
| `agent/device_event_protocol.py` | vendor push envelope/ACK parsing | Identity/recognition/vendor contract | ADAPTER | CORE | BASE | receiver | HTTP adapter | pure values | payload | none | NO | MOVE_LATER |
| `agent/device_event_receiver.py` | FastAPI receiver and safe FaceReco parsing | API + Identity recognition adapter | ADAPTER | CORE (mixed) | BASE/2A | Member API | HTTP | intake, protocol, Face Spatial | request | Inbox through intake | NO | SPLIT_LATER |
| `agent/device_event_worker.py` | leased Inbox delivery worker | Identity/recognition/inbox worker | APPLICATION | CORE | BASE | Identity runtime | runtime | Inbox, compatibility callback | Inbox | Inbox claims/status | NO | KEEP_CURRENT |
| `agent/device_identity.py` | mapping repository, poller, legacy DTOs | Identity/recognition + adapters | ADAPTER | CORE (mixed) | BASE/2A | Identity runtime/API | runtime/member sync | camera API, intake | mappings/cursor | mappings/cursor/Inbox | NO | SPLIT_LATER |
| `agent/device_health.py` | push/poll freshness persistence | Identity/recognition/health | APPLICATION | CORE | BASE | Identity runtime/API | receiver/runtime/ops | health model | health | health | NO | MOVE_LATER |
| `agent/device_camera_registration.py` | event-time Device/Camera registration | Identity/recognition/device mapping | APPLICATION | CORE | 2A | candidate provider/StoreRuntime | Identity | registration model | registrations | registrations | NO | KEEP_CURRENT |
| `agent/store_resolution.py` | event-time Store source resolution/conflict | Identity/recognition/store resolution | APPLICATION | CORE | BASE/2A | Resolver/recovery | Identity/Visit | registration repos | Inbox/Visit/registrations | optional Store projection | NO | KEEP_CURRENT |
| `agent/store_mapping_recovery.py` | retry accepted events missing Store | Identity/recognition/recovery | APPLICATION | CORE | BASE | Identity runtime | runtime | Inbox, StoreResolution | Inbox | Inbox status | NO | KEEP_CURRENT |
| `agent/association_transition.py` | atomic Inbox state-axis transitions | Identity/recognition/transitions | APPLICATION | CORE | MULTI | Resolver/workers | Identity/Visit | Inbox model | Inbox | Inbox state | NO | KEEP_CURRENT |
| `agent/face_spatial_evidence.py` | sanitized Face Spatial DTO/parser | Identity/track_association/spatial evidence | DOMAIN | CORE | 2A | receiver/associator | Identity adapters | pure values | payload | none | NO | KEEP_CURRENT |
| `agent/camera_runtime_registry.py` | active Store/camera/runtime registry | Identity/track_association/runtime registry | APPLICATION | CORE | 2A | Store/Behavior runtime | Runtime | clock | runtime registrations | in-memory registry | NO | KEEP_CURRENT |
| `agent/camera_track_identity.py` | snapshot buffer, candidate provider, associator, exact Cache | Identity/track_association + binding/cache | DOMAIN | CORE (mixed) | 2A/2B | Store/Behavior/Identity | runtimes/workers | registry, mapping, Face Spatial | snapshots/bindings | in-memory snapshots/cache | NO | SPLIT_LATER |
| `agent/track_association_policy.py` | policy, immutable Evaluation and Review audit | Identity/track_association/policy-audit | APPLICATION | CORE | 2A | StoreRuntime/worker | Identity/Operations | ORM/evidence | policy/evaluation | policy/evaluation/review | NO | KEEP_CURRENT |
| `agent/recognition_association_worker.py` | claim Inbox, request assessment, invoke Resolver | Identity/application/track-association worker | APPLICATION | CORE | 2A/2C | Identity runtime | runtime | candidate provider, Resolver | Inbox/Member | Inbox claim/result via Resolver | NO | KEEP_CURRENT |
| `agent/track_association_finalization.py` | Binding application, conflict/recovery, cache/backfill, heartbeat | Identity/binding | APPLICATION | CORE | 2B | StoreRuntime workers | Runtime/Resolver Job | Behavior port, Cache, Visit read, ORM | Job/Evaluation/Visit/Binding | Job/Binding/Inbox/cache | NO | SPLIT_LATER |
| `agent/identity_runtime_lease.py` | Store Lease, activation and fence validation | Runtime/platform/lease | APPLICATION | CORE | BASE/2B | StoreRuntime/Resolver/workers | Runtime/applications | lease ORM | lease/store state | lease/store state | NO | KEEP_CURRENT |
| `agent/behavior_vision.py` | YOLO/ByteTrack, pose, vest role, calibration, overlay | Behavior/tracking adapters + role | ADAPTER | CORE | BASE/2A | Behavior runtime/demo | runtime/scripts | Ultralytics/OpenCV, Behavior contracts | frames/cache identity | Person observations | YES | SPLIT_LATER |
| `agent/behavior_monitor.py` | camera-local Entry/Dwell/Reach state machines | Behavior/entry+dwell+reach | DOMAIN | CORE | BASE | Behavior runtime | Behavior app/tests | Behavior event contracts | TrackObservation | event inputs | YES | SPLIT_LATER |
| `agent/behavior_events.py` | Behavior write/backfill plus Evidence/Traffic queries | Behavior/events + Evidence/Operations | APPLICATION | CORE (mixed) | BASE/2B | Behavior/Finalizer/API | multiple capabilities | ORM, Visit canonical read | Behavior/Visit/Evidence | Behavior/Evidence | YES | SPLIT_LATER |
| `agent/staff_greeting_evaluation.py` | formal greeting application and shadow bridge | Behavior/greeting | APPLICATION | SUPPORT | BASE | Behavior runtime | Behavior app | greeting domain/events | tracks/entries | greeting events | NO | MOVE_LATER |
| `agent/_staff_greeting_shadow.py` | shadow greeting state and persistence | Behavior/greeting/shadow | APPLICATION | SUPPORT | BASE | greeting evaluation | Behavior | Behavior/Entry/Evidence ORM | greeting facts | cycles/evaluations/demo events | NO | MOVE_LATER |
| `agent/customer_entry_visit.py` | Visit Entry writer, exact-cache narrow reuse, legacy identity commands | Visit/entry | APPLICATION | CORE (mixed) | BASE/2C | Behavior/legacy runtime | Behavior app | Visit core, Cache input, legacy helper | Entry/Visit/cache | Entry/Evidence/Visit/activity | NO | SPLIT_LATER |
| `agent/_customer_visit_identity.py` | legacy identity attachment helper | Legacy/visit identity | APPLICATION | LEGACY | BASE | CustomerEntryVisit legacy methods | legacy path | Member/Visit ORM | identity evidence | Visit/Member link | NO | LEGACY_ISOLATE |
| `agent/reusable_visit_resolver.py` | fenced Recognition-to-Visit orchestration and Phase 2C | Visit/resolver + convergence | APPLICATION | CORE | BASE/2B/2C | Identity worker | Identity application | Visit merge, Effects/Member/Binding repos | Inbox/Evaluation/Visit/Arrival/Binding | Visit/Arrival/Effect/Job/projection job | NO | SPLIT_LATER |
| `agent/visit_merge_service.py` | canonical selection and audit-preserving fact convergence | Visit/merge | APPLICATION | CORE | BASE/2C | Resolver/repair | Visit applications | ORM, Member stats | Visit/business facts | supersede/move facts | NO | SPLIT_LATER |
| `agent/visit_activity.py` | coalesced confirmed Track activity refresh | Visit/activity | APPLICATION | CORE | 2B-Step4 | Store/Behavior runtime | Runtime | exact projection input, Visit repo | Visit | `last_seen_at` only | NO | KEEP_CURRENT |
| `agent/association_projection.py` | rebuildable association outcome worker | Visit/projection | APPLICATION | CORE | BASE | Store/Identity runtime | Runtime/Resolver jobs | Inbox/Visit/Arrival | canonical facts | Inbox projection/job status | NO | KEEP_CURRENT |

## Member, Effects, Evidence, Profile, Operations, API, Infrastructure

| Current File | Current Actual Responsibility | Owning Capability / Target Submodule | Layer | Class | Phase | Production Caller | Incoming | Outgoing | Fact Read | Fact Write | Phase2D | Future Action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent/member_priority.py` | tier normalization/ranking | Member/domain/priority | DOMAIN | CORE | BASE | many modules | Member/Behavior/Ops | SQL helper | tier | none | NO | KEEP_CURRENT |
| `agent/member_statistics.py` | stats from Effective Arrivals | Member/application/statistics | APPLICATION | CORE | BASE/2C | Resolver/Merge/repair | Visit apps | Member/Arrival/Visit repo | effective arrivals | Member projections | NO | KEEP_CURRENT |
| `agent/member_management_service.py` | CRUD, face lifecycle, member queries | Member/application/management | APPLICATION | SUPPORT (mixed) | BASE | API | API | Member/device sync/legacy query | Member/business rows | Member/mapping/jobs | NO | SPLIT_LATER |
| `agent/member_device_sync.py` | authorize/revoke camera identity mapping | Member/application/device sync | APPLICATION | SUPPORT | BASE | management | Member app | camera/mapping ports | Member mapping | device mapping | NO | MOVE_LATER |
| `agent/member_service.py` | member/history/preferences plus legacy visit writer | Member/application + Legacy writer | APPLICATION | SUPPORT (mixed) | BASE | management/legacy agent | Member/Legacy | ORM | Member/history | prefs/points/legacy Visit | NO | SPLIT_LATER |
| `agent/member_arrival_effects.py` | Effect claim/authorize/ACK/fail ledger | Effects/application/consumer | APPLICATION | CORE | BASE | future consumer/tests | Effects | Arrival/Visit repo | effect/arrival | effect delivery state | NO | SPLIT_LATER |
| `agent/device_recordings.py` | recording index/download/trim and clip processor | Evidence/recordings | ADAPTER | CORE | BASE | API/evidence | API/tools | vendor/media/ORM | recordings/clip | clip status/path | ONLY_IF_BLOCKING | MOVE_LATER |
| `agent/device_capture_store.py` | bounded JPEG capture storage | Evidence/capture adapter | ADAPTER | CORE | BASE | Runtime/API/Intake | adapters | filesystem | bytes/path | capture files | ONLY_IF_BLOCKING | MOVE_LATER |
| `agent/customer_profile_analyzer.py` | offline keyframe/crop/profile analysis | Profile/analysis adapter | ADAPTER | SUPPORT | BASE | profile pipeline | tools/profile | OpenCV/Ollama | media | files/result | NO | MOVE_LATER |
| `agent/profile_jobs.py` | leased profile jobs | Profile/application/jobs | APPLICATION | SUPPORT | BASE | legacy business runtime | legacy runtime | ORM/member priority | jobs/member/legacy entry | job state | NO | MOVE_LATER |
| `agent/profile_snapshot_store.py` | transient crop storage/TTL | Profile/snapshot adapter | ADAPTER | SUPPORT | BASE | legacy runtime | profile | filesystem | snapshots | snapshots | NO | MOVE_LATER |
| `agent/llm_service.py` | mock/Ollama text/vision adapters | Infrastructure/LLM adapters | ADAPTER | SUPPORT | BASE | profile/tools | profile | config/schema/Ollama | prompts | model result | NO | MOVE_LATER |
| `agent/operations_query_service.py` | read-only health/recognition/profile views | Operations/queries | APPLICATION | SUPPORT | BASE/2A | API | API | ORM/member priority | operational facts | none | YES | MOVE_LATER |
| `agent/member_behavior.py` | MB-01A member/scope policy and safe summary/timeline projections | Operations/member behavior queries | APPLICATION/PORT | CORE | MB-01A | MCP adapter | MCP server/tests | capability-local read port, StoreContext | confirmed Offline member behavior DTOs | none | NO | KEEP_CURRENT |
| `agent/member_behavior_repository.py` | SQLAlchemy implementation of the MB-01A read port and frozen eligibility predicate | Infrastructure/member behavior query adapter | ADAPTER | CORE | MB-01A | MCP composition | Member behavior application | ORM/session | Member and BehaviorEvent rows | none | NO | KEEP_CURRENT |
| `agent/member_behavior_mcp.py` | two strict MB-01A MCP schemas and request adapters | API/member behavior MCP | ADAPTER | CORE | MB-01A | MCP dispatcher | MCP server | Member behavior query interface | safe query results | none | NO | KEEP_CURRENT |
| `agent/staff_greeting_shadow_query.py` | read-only greeting comparison | Operations/greeting queries | APPLICATION | SUPPORT | BASE | API | API | greeting/evidence ORM | greeting facts | none | NO | MOVE_LATER |
| `agent/analyzer.py` | heatmap/dwell SQL analytics | Operations/analytics | APPLICATION | SUPPORT | BASE | tools/tests | support | ORM | zone stays | none | YES | MOVE_LATER |
| `agent/member_api.py` | FastAPI routes plus service composition | API + composition | ADAPTER | SUPPORT (mixed) | BASE | web server | HTTP | Member/Ops/Evidence/Identity/DB | many facts via queries | through commands | YES | SPLIT_LATER |
| `agent/camera_face_api.py` | vendor camera client/XML | Infrastructure/camera vendor | ADAPTER | CORE | BASE | Identity/Member/Evidence | multiple | requests/XML | device | external device | NO | MOVE_LATER |
| `agent/config.py` | environment normalization | Infrastructure/config | INFRASTRUCTURE | CORE | BASE/2B | all roots | process env | settings libs | env | none | ONLY_IF_BLOCKING | KEEP_CURRENT |
| `agent/db.py` | engine/session/schema startup | Infrastructure/database | INFRASTRUCTURE | CORE | BASE | roots/API/tests | all persistence | SQLAlchemy/schema evolution | schema/config | schema startup | NO | KEEP_CURRENT |
| `agent/models.py` | all capability ORM entities/indexes | Infrastructure transitional schema; fact-owned later | INFRASTRUCTURE | CORE | MULTI | nearly all persistence | all capabilities | SQLAlchemy | all rows | all row shapes | NO | SPLIT_LATER |
| `agent/schema_evolution/__init__.py` | migration facade | Infrastructure/schema runner | INFRASTRUCTURE | CORE | MULTI | DB startup | db | migration modules | schema | schema | NO | KEEP_CURRENT |
| `agent/schema_evolution/_schema.py` | ordered migration orchestration | Infrastructure/schema runner | INFRASTRUCTURE | CORE | MULTI | migration facade | db | capability migrations | schema | schema | NO | SPLIT_LATER |
| `agent/schema_evolution/_recognition.py` | Inbox/association/binding migrations/indexes | Identity persistence adapter | INFRASTRUCTURE | CORE | BASE/2A/2B | schema runner | migration | SQL | schema/data | schema/index repair | NO | MOVE_LATER |
| `agent/schema_evolution/_entry.py` | Entry/Visit migration and readiness | Visit persistence adapter | INFRASTRUCTURE | CORE | BASE/2C | schema runner | migration | SQL | schema/data | schema/index repair | NO | MOVE_LATER |
| `agent/schema_evolution/_behavior.py` | Behavior schema evolution | Behavior persistence adapter | INFRASTRUCTURE | CORE | BASE/2B | schema runner | migration | SQL | schema | schema | NO | MOVE_LATER |
| `agent/schema_evolution/_members.py` | Member schema evolution | Member persistence adapter | INFRASTRUCTURE | CORE | BASE | schema runner | migration | SQL | schema | schema | NO | MOVE_LATER |
| `agent/schema_evolution/_common.py` | migration bookkeeping/helpers | Infrastructure/schema common | INFRASTRUCTURE | CORE | MULTI | migration modules | migrations | SQL | schema | schema metadata | NO | KEEP_CURRENT |
| `agent/schemas.py` | profile/operations Pydantic result schemas | Profile + Operations contracts | PORT | SUPPORT | BASE | LLM/pipelines | support | Pydantic | DTO | none | NO | SPLIT_LATER |

## Legacy, repair, and tools

| Current File | Current Actual Responsibility | Owning Capability / Target Submodule | Layer | Class | Phase | Production Caller | Incoming | Outgoing | Fact Read | Fact Write | Phase2D | Future Action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent/entry_event_coordinator.py` | old best-effort track/recognition matching | Legacy/entry coordinator | APPLICATION | LEGACY | BASE | legacy runtime only | legacy | legacy identity DTOs | volatile events | callbacks | NO | LEGACY_ISOLATE |
| `agent/face_recognizer.py` | local face recognition fallback | Legacy/local identity | ADAPTER | LEGACY | BASE | old agent | legacy | Member ORM/face lib | Member encodings | encoding files/cache | NO | LEGACY_ISOLATE |
| `agent/detector.py` | shared capture helper plus old detector/SimpleTracker | Infrastructure/capture + Legacy/tracking | ADAPTER | CORE/LEGACY mixed | BASE | Behavior capture and legacy pipelines | mixed | OpenCV/Ultralytics/config | frames | detections | ONLY_IF_BLOCKING | SPLIT_LATER |
| `agent/member_entry_service.py` | legacy MemberEntry/Visit/profile finalization | Legacy/member entry | APPLICATION | LEGACY | BASE | legacy runtime | legacy | Member/Visit/Job ORM | legacy facts | legacy Visit/jobs | NO | LEGACY_ISOLATE |
| `agent/recommendation.py` | old recommendation/coupon side effects | Legacy/recommendation | APPLICATION | LEGACY | BASE | old agent | legacy | Member preference/purchase ORM | Member/history | coupons/logs | NO | LEGACY_ISOLATE |
| `agent/router.py` | old task dispatcher | Legacy/router | APPLICATION | LEGACY | BASE | old callers | legacy | pipelines | request | downstream | NO | LEGACY_ISOLATE |
| `agent/agent.py` | old LangGraph vision agent | Legacy/vision agent | APPLICATION | LEGACY | BASE | package/demo | old entry | detector/face/member/recommendation | broad | broad legacy | NO | LEGACY_ISOLATE |
| `agent/member_arrival_repair.py` | explicit dry-run/apply Arrival repair | Tools/migration | APPLICATION | TOOL | BASE/2C | repair CLI/tests | operator | Visit/Arrival/Effects/Stats | durable facts | authorized repair | NO | MOVE_LATER |
| `agent/store_mapping_recovery.py` | production retry for missing Store | Identity/recognition/recovery | APPLICATION | CORE | BASE | Identity runtime | runtime | StoreResolution/Inbox | Inbox | Inbox status | NO | KEEP_CURRENT |
| `agent/video_aggregator.py` | offline video result aggregation | Tools/analysis | APPLICATION | TOOL | BASE | historical pipeline | tools | schemas | analysis rows | report DTO | NO | MOVE_LATER |
| `agent/yolov8n.pt` | bundled model asset | External/dev asset | ADAPTER | SUPPORT | BASE | detector | model loader | filesystem | weight file | none | NO | MOVE_LATER |

## Standalone VLX video experiment (2026-10-27)

These physical files are a user-requested offline tool, not an enablement of
Production/Offline V1 or a change to frozen Stage5/7C/8/9 contracts.

| File | Logical owner | Responsibility |
| --- | --- | --- |
| `agent/vlx_interaction.py` | Behavior/merchandise/interaction | Media-time action assessment and in-memory statistics |
| `agent/vlx_person_tracking.py` | Behavior/tracking | Video-local anonymous user association |
| `scripts/vlx_interaction_vision.py` | Tools/vision adapters | VLX grounding, optical flow and wrist observations |
| `scripts/run_vlx_interaction_video.py` | Tools/analysis composition | File decoding, presentation timeline and result export |

The rule modules have no ORM or model dependencies. The standalone composition
imports the concrete adapters; production modules do not import this experiment.

## Mapping conclusions

1. `StoreRuntime` 只应保留 composition、lease context、fatal supervision 和 shutdown；
   module-specific worker construction应逐步交给 factories。
2. `ReusableVisitResolver` 的 transaction orchestration 合法，但 concrete Identity、Effects、
   Member 和 ORM 依赖应逐步换成窄 Port。
3. `TrackAssociationFinalization` 属于 Identity/Binding；Behavior backfill 与 Visit read
   必须通过外部 module Interface，而不是变成 Binding 所有事实。
4. `CustomerEntryVisit` 是 Visit Entry Fact Writer；legacy identity command 必须最终隔离。
5. `agent.models` 最后拆；在 E2E 前移动它会同时破坏所有 capability。
