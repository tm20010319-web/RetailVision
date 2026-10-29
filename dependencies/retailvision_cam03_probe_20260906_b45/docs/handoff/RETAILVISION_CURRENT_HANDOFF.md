# RetailVision Current Handoff

Document date: 2026-08-20

This is the starting document for a new Codex session. Read it with root
`AGENTS.md`, `docs/architecture/v2/README.md`, relevant ADRs/`CONTEXT.md`, and
the modules named below before changing code. The current logical architecture
is Target Architecture v2. It does not authorize package migration, a new
Offline V1 design, or a production release.

## Executive Status

RetailVision analyzes P6S camera recordings offline to derive product behavior:

```text
P6S RecordingFile
  -> authoritative Presentation-time sampling
  -> SAM3.1 Person Tracking (Stage5)
  -> SAM3.1 Product Tracking (Stage7C)
  -> Person/Product Association (Stage8)
  -> PICK_UP / HOLDING / PUT_DOWN (Stage9)
  -> normalized behavior facts
  -> artifact / formal publication when the relevant gates allow it
```

Identity is an independent WHO chain:

```text
P6S FaceReco -> durable Recognition Evidence -> historical member identity
  -> temporal Primary Person Snapshot -> verified Face/Recording geometry
  -> Track Identity -> confirmed member projection only when confirmed
```

The governing rule is: **behavior may be anonymous; identity must not be
guessed**. The WHAT and WHO chains remain decoupled. A failed identity result
does not invalidate a detected product interaction, but it must never inject a
member into a formal behavior fact.

## Current Product Analytics MCP state

R5-04.1 is implementation-complete. The Python MCP boundary now owns one
fail-closed ProductCatalog resolver for public aliases and canonical keys.
Unknown products return `product_key_unknown` before any query. The generic
Java Agent uses only the terminal no-tool Provider round as the final answer;
earlier planning text remains a separately observable preamble. No
RetailVision routing, alias, tool override, or answer ownership was added to
Java.

The Product Analytics MCP remains default-off, bounded to deployment-owned
store/camera/window, read-only, schema-ready-only, and limited to 31 days. It
is ready for bounded internal Canary. The final R5-04.1 real evidence records
17/21 overall across batches 7/7, 6/7, and 4/7, with 21/21 protocol, 19/21
grounding, 19/21 presentation, 26 MCP calls, 21 invocations, zero retries,
zero stream/argument failures, and zero unknown product keys. One batch 2
grounding result is a retained checker false positive fixed after execution;
historical evidence was not relabeled. Three real batch 3 answer failures
remain, so production enablement is blocked. The detailed historical report is
archived outside the repository at
`D:\GJJ\work\retailvision_archive\20260826\duplicates\docs\reports\R5_04_1_AUTONOMOUS_MCP_ACCEPTANCE_SEMANTIC_INTEGRITY_REPORT.md`.

## Current B2 Qualification State

```text
B2.1 RecordingFile bytes                         = VERIFIED
Historical Registration Scope                    = VERIFIED for authorized interval
Historical QueryRecordList on cam02              = unsupported
Durable Push Inbox Provider                      = VERIFIED
B2.3 Full Segment Boundary                       = NOT VERIFIED
B2.3C Guarded Interior Event-Time (5 seconds)   = VERIFIED
Timezone / device clock / Event Time -> PTS      = VERIFIED
Historical Identity                              = VERIFIED
B2.6 Track Identity real-device                  = VERIFIED (Event 701)
B2.6A Face Spatial Geometry                      = VERIFIED
B2.7 scoped P6S Capability Profile               = VERIFIED
B2.8 Event 804 behavior qualification            = PASS
B2.8 Trusted Production Publication              = NOT VERIFIED
Provider A Production                            = FAIL-CLOSED
```

`B2.8` is blocked only for the Event 804 real-run qualification because its
same-run Track Identity is `unmatched`, not because the behavior analyzer,
publication fence, or EOF sampling implementation failed. Do not enable
Provider A Production or start a Final Production Release Gate.

## Event 804: Real Behavior Result

Scope and source:

```text
device_id              = cam02 (192.0.2.6)
analysis_job_id        = b28-real-804-flush
analysis_run_id        = b28-real-804-flush-run
recording              = AI_20260820122257-20260820122501.mp4
source_video_sha256    = 5c75769856cb1175b958a884478b30e72a82bfe8e1c36be223ca10e66693222a
recording interval UTC = 2026-08-20T04:22:57 to 2026-08-20T04:25:01
```

The real operator performed two distinct interactions by one person with the
same `smart_toy_a`:

```text
Session 1: PICK_UP -> HOLDING -> PUT_DOWN
Session 2: PICK_UP -> HOLDING -> PUT_DOWN
```

Stage9 matched that operation exactly. Current real-run evidence is:

```text
sample_count                     = 621
provider_processed_sample_count  = 621
terminal_pending_samples         = 0
provider_sample_coverage_status  = complete
interaction_sessions             = 2
normalized_fact_count            = 6
behavior qualification           = PASS
```

This is not a duplicate or an unwanted session split. Multiple sequential,
complete sessions by the same person for the same product in one Run are valid
and must not be deduplicated. The job-level publication fence prevents a Run
from publishing twice; it does not suppress a second real pickup of the same
product.

### Stage9 Fact Contract

```text
PICK_UP at T1                 -> merchandise_pick_up
PICK_UP at T1 + PUT_DOWN T2   -> merchandise_holding [T1, T2]
PUT_DOWN at T2                -> merchandise_put_down
```

Every valid closed interaction session therefore emits three normalized facts.
Event 804 emits six. No EOF or Track Lost path may synthesize a `PUT_DOWN` or
a completed holding interval.

The artifacts contain product key/track token, person track token, interaction
session ID, pickup/put-down times, holding interval/duration, and enough raw
facts to derive pickup count, put-down count, and session count. They prove
the two `smart_toy_a` interactions were detected and recorded. They are **not**
formal trusted `BehaviorEvent` rows because Event 804 has not passed B2.8.

### EOF Terminal Flush

The initial Event 804 probe selected 621 samples but processed 620, leaving one
real sample pending at EOF. The implementation now sends only the remaining
real samples through an explicit terminal SAM3 provider window. It does not:

```text
duplicate a source frame
pad a fake frame
create a synthetic sample
alter source_pts or an authoritative timestamp
```

Normal windows still require 12 contexts. A terminal post-overlap window must
contain at least one fresh source sample. Relevant code is
`agent/sam31_realtime_providers.py` and
`scripts/run_offline_complete_loop_golden.py`; focused regression is
`test/test_b28_qualification_semantics.py`.

## Event 804: Identity Known Limitation

Fixed evidence:

```text
source_event_id       = 804:2026-08-20T04:23:19
canonical_event_id    = ae87a0d9d50eaa308ae79f244c41d7973e3cd4306a143b62034884f0949ab1ef
member candidate      = member-gaojunjie (restricted evidence only)
recognition time UTC  = 2026-08-20T04:23:19
Primary track         = store-1:cam02:b28-real-804-flush-run:1
Primary source_pts    = 22.031048 seconds
Primary delta         = 31.048 ms
Primary policy        = minimum absolute temporal delta
```

The Primary snapshot is selected by time before spatial evaluation. It is not
selected by the most favorable containment in the +/-0.5 second support window.
Temporal support is diagnostic only.

Coordinate spaces were specifically checked:

```text
FaceReco BackgroundImage      = 1920 x 1088
Recording decoded frame       = 800 x 448
SAM3/Stage5 AI inference      = 1280 x 720
Person representation         = normalized_full_frame
Binder comparison             = normalized_full_frame after verified Face mapping
Geometry policy               = p6s_face_recording_geometry_v1
Transform                     = verified full_frame_resize, no crop/padding/mirror/rotation
```

There is no raw `800x448` Face pixel bbox compared directly against a raw
`1280x720` Person pixel bbox, and no duplicate normalization was found. The
debug image uses the Run's PTS time base `4967 / 124000` and does show the
Primary frame at approximately 22.031048 seconds.

Actual primary geometry:

```text
mapped Face bbox normalized    = [0.38125, 0.345588, 0.48125, 0.522059]
Primary Person bbox normalized = [0.354688, 0.391667, 0.535938, 0.922222]
Face center inside Person      = true
upper-body check               = true
face/person containment        = 0.7388888889
frozen containment threshold   = 0.8
```

Diagnosis is `valid_geometry_non_match`. The following were ruled out for
this event: temporal mismatch, geometry scope mismatch, coordinate-space bug,
primary-person instability, and wrong Track candidate. The two behavior
sessions also use this same Track token.

Therefore:

```text
track_identity       = unmatched / not confirmed
member projection    = blocked
artifact seal        = not attempted for this B2.8 real qualification
formal publication   = not attempted
B2.8                 = NOT VERIFIED
```

Do not change the `0.8` threshold, Primary policy, geometry contract, Face
bbox, Person bbox, event time, Track ID, or Stage5/7C/8/9 merely to make Event
804 confirmed. Register this as a Known Limitation/backlog item. Any future
work on a more robust spatial-evidence policy is a separately approved
qualification phase, not a quiet modification of this contract.

This does not mean Track-to-Member is unimplemented: real Event 701 previously
proved `FaceReco -> Primary Person Track -> geometry -> member-gaojunjie ->
confirmed Track Identity`. The capability exists; Event 804's particular
Primary spatial evidence does not meet it.

## Publication Boundary

The existing formal chain is retained:

```text
OfflineBehaviorPipeline
  -> OfflineBehaviorIdentityProjector
  -> OfflineRunArtifactStore (validate, hash, verify, seal)
  -> canonical event manifest
  -> OfflineBehaviorPublisher / OfflineBehaviorPublication
  -> BehaviorEventStore
```

`TrustedOfflinePublicationGate` is an optional B2.8 qualification gate. A
real formal B2.8 publication requires, for the same Run: trusted source bytes,
registration scope, resolved scoped capability profile, complete Push coverage,
authoritative Presentation timeline and guarded Event time, historical identity,
confirmed behavior-bearing Track identity, runtime geometry compatibility,
valid normalized facts, artifact verification/seal, and `publication_eligible`.
All failures are fail-closed.

Formal `BehaviorEvent.member_id` may be projected only from a Track Identity
with status `confirmed`. `probable`, `ambiguous`, `conflict`, `unmatched`, and
`unavailable` must project `member_id = null`; candidate member and Face bbox
remain restricted diagnostic evidence and must never enter ordinary event
details/UI/API.

The formal-event isolation remains:

```text
visit_id         = null
entry_event_id   = null
evidence_clip_id = null
```

Do not create Visit, Arrival, Welcome, Coupon, live binding, or EvidenceClip
as part of Offline V1/B2 work.

## Actual Persisted Tables

These names come from `agent/models.py`; do not infer a new source of truth.

| Table | Role now |
| --- | --- |
| `behavior_events` | Formal normalized business events, including `member_id`, track token, time, duration, details and optional provenance. Event 804 has no rows from this qualification. |
| `offline_behavior_publications` | Immutable job-level publication fence: one `analysis_job_id`, published Run, event count, source video ID, and event-manifest hash. |
| `device_recognition_inbox` | Durable sanitized P6S recognition evidence, BackgroundImage/Face spatial evidence, device metadata, and association state. |
| `device_recognition_cursors` | Durable cursor for completed recognition polling. |
| `push_coverage_observations` | Evidence that device Push coverage is complete for the required interval. |
| `device_member_mappings` | Current device-to-member mapping projection. Never use it as a historical fallback. |
| `device_member_identity_revisions` | Immutable Event-Time history used for historical identity resolution. |
| `track_association_evaluations` | Immutable audit results for temporal/spatial Track association assessment. |
| `track_identity_bindings` | Durable confirmed binding lifecycle/audit records; only confirmed identity is eligible for safe projection. |
| `track_association_finalization_jobs` | Leased association finalization workflow state. |
| `device_store_registrations`, `camera_store_registrations`, `device_camera_registrations` | Historical device/camera/store registration scope gates. |

Run artifacts are files produced by `OfflineRunArtifactStore`, including
normalized facts and restricted identity artifacts. They are evidence, not a
new analytics database table and not equivalent to a formal `BehaviorEvent`.

## Frozen Boundaries

### Behavior and sampling

Do not modify Stage5 Person semantics, Stage7C Product semantics, Stage8
association semantics, Stage9 interaction semantics, or their current
thresholds. Preserve authoritative Presentation Time sampling. Never create
duplicate frames, synthetic samples, fake timestamps, or a copied last frame to
fill a provider window.

### Identity

Keep the statuses `confirmed`, `probable`, `ambiguous`, `conflict`, `unmatched`,
and `unavailable`. Repeated probable evidence cannot vote itself into confirmed.
Only confirmed identity can supply formal `member_id`. Primary is chosen by
nearest legitimate time, then geometry is evaluated; spatial success cannot
choose a replacement Primary.

### Qualification and production

The guarded-interior policy remains 5 seconds:

```text
[recording start + 5s, recording end - 5s)
```

Full Segment Boundary remains `NOT VERIFIED`. The cam02 `QueryRecordList`
historical path remains unsupported. Device scope is cam02 / `192.0.2.6`,
serial `I20F11AB92E67AA9FE29D3D`, model `T5AI-W/IPG-5950PCS-AI`, firmware
`3.4.0-20260430-EN`, runtime protocol contract `p6s-http-v1`.

## Evidence and Key Files

| Path | Purpose / current state |
| --- | --- |
| `P6S_TRUSTED_PRODUCTION_PUBLICATION_VALIDATION.md` | B2.8 automated and real-run qualification history; Event 804 behavior PASS but real publication not verified. |
| `P6S_EVENT_804_SPATIAL_IDENTITY_DIAGNOSTIC.md` | Authoritative read-only explanation of Event 804 `valid_geometry_non_match`. |
| `reports/p6s_trusted_publication_20260820/event_804/controlled_run.json` | Source, guard, behavioral qualification and before/after flush evidence. |
| `reports/p6s_trusted_publication_20260820/event_804/behavior_probe_flush.json` | Real 621/621 Stage5/7C/8/9 probe and normalized facts. Restricted diagnostic artifact. |
| `reports/p6s_trusted_publication_20260820/event_804/identity_publication_gate.json` | Same-run identity/publication decision: not eligible. |
| `reports/p6s_trusted_publication_20260820/event_804/event_804_spatial_identity_diagnostic.json` | Machine-readable temporal, coordinate, geometry and Track diagnostic. |
| `scripts/diagnose_event_804_spatial_identity.py` | Read-only restricted diagnostic renderer; uses presentation time base for decoded-frame selection. |
| `agent/sam31_realtime_providers.py` | SAM3 provider-window contract, including the bounded real-sample terminal window. |
| `scripts/run_offline_complete_loop_golden.py` | Frozen complete-loop runner and EOF flush orchestration. |
| `scripts/probe_b28_real_behavior.py` | Parameterized restricted real behavior probe and multi-session qualification check. |
| `agent/offline_behavior.py` | Stage9 raw-to-normalized fact adapter and confirmed-only behavior identity projection. |
| `agent/offline_publication.py` | Trusted B2.8 dynamic gate, canonical event construction and publisher/fence rules. |
| `agent/offline_identity_association.py` | Offline event time/geometry mapping and Primary association orchestration. |
| `agent/camera_track_identity.py` | Same-camera binder and frozen spatial containment policy. |
| `pipelines/offline_behavior_pipeline.py` | Offline source, media, behavior, identity, artifacts and publication orchestration. |
| `agent/models.py` | Actual ORM table names and persisted domain facts. |

The Primary and temporal-support debug images are restricted diagnostics under
`reports/p6s_trusted_publication_20260820/event_804/`. Do not expose the
candidate member identity in general UI/API output.

## Test Baseline

Latest documented qualification results:

```text
B2.8 focused publication/pipeline tests                 = PASS (23/23)
Offline artifact/behavior/identity/media/publication    = PASS (102/102 focused)
Stage5/7C/8/9 regression                                = PASS (33/33)
Fingerprint-scoped device/recording selected regression = PASS (86/86)
EOF terminal-window focused tests                       = PASS (3/3)
```

The current session additionally reran the 3 terminal-window unit tests and
compiled the Event 804 diagnostic script. Do not describe these focused results
as a fully green repository. The older complete-loop golden report records a
full-repository baseline with known unrelated failures/import error; rerun and
classify the full baseline before claiming a global result.

## Known Issues and Backlog

1. Event 804 Face/Person containment is `0.7388888889 < 0.8`; it remains
   `unmatched` under the frozen policy.
2. B2.8 Trusted Production Publication is not verified because that event lacks
   confirmed same-run Track Identity. No real formal publication was attempted.
3. Provider A Production remains fail-closed.
4. Full Segment Boundary Contract is not verified.
5. cam02 QueryRecordList historical query is unsupported.
6. Product Behavior Analytics needs a clearly approved data boundary for legal
   anonymous behavior facts. No new analytics persistence source exists yet.

## Next Mainline: Product Behavior Analytics V1 (Design First)

The next session must not reopen Event 804 geometry or rerun Stage5/7C/8/9.
First explain to the user, without changing code:

1. Current `PICK_UP`/`HOLDING`/`PUT_DOWN` facts live in Run artifacts for Event
   804; formal `behavior_events` were not written because B2.8 requires a
   confirmed Track identity and Event 804 is unmatched.
2. Anonymous product statistics should not be semantically blocked by an
   unavailable member identity. `person_track_token` is sufficient to derive a
   scoped unique-person-track metric.
3. The source-of-truth boundary must be approved before implementation.

Present and compare these choices:

```text
A. Aggregate only formal BehaviorEvent rows.
   Clean publication fence, but Event 804 anonymous facts are excluded.

B. Admit qualified anonymous normalized behavior facts to a separate approved
   analytics ingestion/projection boundary, explicitly separate from member
   identity publication and with its own idempotency/audit contract.
```

Do not silently create a second fact table, query raw artifacts directly from
an LLM, or let Identity failure block anonymous analytics. After user approval,
the minimal V1 should support filters by `store_id`, `camera_id`, `product_key`,
and time range, with `pick_up_count`, `put_down_count`,
`completed_interaction_count`, `total_holding_seconds`, `avg_holding_seconds`,
`max_holding_seconds`, `unique_person_track_count`, and
`last_interaction_at`. The intended layering is:

```text
approved BehaviorEvent / approved anonymous analytics projection
  -> Product Interaction Aggregation / Query Service
  -> future MCP query_product_interaction_stats
  -> Store Agent
```

The Agent must use this query service, not scan raw Stage9 artifacts or embed
database queries in prompts.

## New Session Prompt

```markdown
Read RETAILVISION_CURRENT_HANDOFF.md, root AGENTS.md, and the key modules it
lists. Do not modify code. Explain where current PICK_UP/HOLDING/PUT_DOWN facts
are stored, why Event 804 behavior PASS has no formal BehaviorEvent publication,
and whether anonymous behavior analytics should be blocked by the member
Identity gate. Propose Product Behavior Analytics V1: approved data source,
table/projection boundaries, aggregation logic, query interface, frozen
contracts, and an implementation prompt. Wait for approval before development.
```
