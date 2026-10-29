# RetailVision Development Worklog Index

Status: `CURRENT_AUTHORITY`
Role: `DEVELOPMENT_CONTINUITY_INDEX`
Content authority: `ROUTING_ONLY`

## Purpose

This index helps a new development session find recent project continuity
information without loading every historical worklog. It points to worklogs;
it does not copy their decision or business content.

## Required Reading Order

Before starting development, read:

1. [`DOCUMENT_AUTHORITY_REGISTRY.md`](../authority/DOCUMENT_AUTHORITY_REGISTRY.md)
   for document routing and authority resolution.
2. This index for the latest relevant worklog.
3. The latest relevant worklog for recent state, decisions, and next steps.

Do not read all historical worklogs by default. Select the latest worklog that
matches the area being changed, then follow its links as needed.

## Naming Convention

Use one Markdown file per development continuity entry:

`WORKLOG_<YYYYMMDD>_<phase-or-topic>.md`

Examples:

- `WORKLOG_20260824_R4-08_governance.md`
- `WORKLOG_20260824_product-analytics-mcp.md`

Use the repository date for `YYYYMMDD`, keep the phase or topic short, and use
lowercase kebab-case for descriptive topic text. Do not overwrite an earlier
worklog; add a new dated entry when the state materially changes.

## Indexing Rules

Each worklog entry should be added to the table below with:

- date;
- phase or topic;
- one-line state summary;
- link to the worklog; and
- status such as `ACTIVE`, `COMPLETED`, `BLOCKED`, or `HISTORICAL`.

Keep entries newest first. Update the `Latest phase pointer` when a new phase
or materially changed project state is recorded. Keep older entries in the
table for traceability; do not delete them during routine updates.

## Latest Phase Pointer

Latest recorded phase: **DPI-01E cam03 Offline Member Binding**

Status: `BLOCKED / RECORDING_TIME_PROVENANCE_PENDING`

Current worklog:
[`WORKLOG_20260905_DPI-01E_offline-member-binding.md`](WORKLOG_20260905_DPI-01E_offline-member-binding.md)

Previous current worklog:
[`WORKLOG_20260905_DPI-01D_new-video-initialization-review.md`](WORKLOG_20260905_DPI-01D_new-video-initialization-review.md)

Previous source-video diagnosis:
[`WORKLOG_20260905_DPI-01D1_ai-guitar-putdown-diagnosis.md`](WORKLOG_20260905_DPI-01D1_ai-guitar-putdown-diagnosis.md)

Previous worklog:
[`WORKLOG_20260905_DPI-01D_full-behavior-replay.md`](WORKLOG_20260905_DPI-01D_full-behavior-replay.md)

Previous phase: **DPI-01C.3 Fixed Display Position Evidence Gate**

For the previous source video, cam03 used `fixed_support_contact` with
operator-confirmed Masks, mappings, Surface, support points and tolerance.
That confirmation does not apply to the newly submitted video. Legacy cam02
continues to default to `mask_surface_overlap`.

Historical phase worklog:
[`WORKLOG_20260905_DPI-01C3_fixed-display-position.md`](WORKLOG_20260905_DPI-01C3_fixed-display-position.md)

Previous phase: **DPI-01C.2 cam03 Action-Window Diagnostic**

Previous worklog:
[`WORKLOG_20260905_DPI-01C2_action-window-diagnostic.md`](WORKLOG_20260905_DPI-01C2_action-window-diagnostic.md)

Preceding worklog:
[`WORKLOG_20260904_DPI-01C2_real-propagation.md`](WORKLOG_20260904_DPI-01C2_real-propagation.md)

Preceding worklog:
[`WORKLOG_20260904_DPI-01C1_two-guitar-real-probe.md`](WORKLOG_20260904_DPI-01C1_two-guitar-real-probe.md)

Earlier preceding worklog:
[`WORKLOG_20260904_DPI-01C1_cam03-config-and-surface.md`](WORKLOG_20260904_DPI-01C1_cam03-config-and-surface.md)

The preceding phase worklog remains available at
[`WORKLOG_20260826_R5-04-1_autonomous-mcp-semantic-integrity.md`](WORKLOG_20260826_R5-04-1_autonomous-mcp-semantic-integrity.md).

Earlier worklogs remain indexed here for continuity and traceability. Only the
latest worklog is required for startup; older entries remain available until a
separate owner-approved worklog compaction decision.

## Worklog Entries

| date | phase/topic | status | summary | worklog |
| --- | --- | --- | --- | --- |
| 2026-09-05 | DPI-01E cam03 Offline Member Binding | BLOCKED / RECORDING_TIME_PROVENANCE_PENDING | Audited the frozen DPI-01D replay and existing Offline Recognition chain; preserved all 12 events as anonymous facts and withheld Recognition/member projection because video overlay time conflicts with filename/container metadata. | [`WORKLOG_20260905_DPI-01E_offline-member-binding.md`](WORKLOG_20260905_DPI-01E_offline-member-binding.md) |
| 2026-09-05 | DPI-01D New Video Full Replay | COMPLETED / DUAL-PRODUCT BEHAVIOR PASS | New 81s video was source-bound, confirmed, and replayed to EOF with 394/394 PTS samples; three electric-guitar and one AI-guitar cycles closed with stable product tokens and a Chinese diagnostic video. | [`WORKLOG_20260905_DPI-01D_new-video-initialization-review.md`](WORKLOG_20260905_DPI-01D_new-video-initialization-review.md) |
| 2026-09-05 | DPI-01D.1 AI吉他 PUT_DOWN Diagnosis | DIAGNOSIS COMPLETE / NO SOFTWARE FIX JUSTIFIED | Detailed 120s-to-EOF replay shows no confirmed return to the AI guitar's fixed support point; Track remains continuous and Stage9 correctly stays fail-closed without PUT_DOWN. | [`WORKLOG_20260905_DPI-01D1_ai-guitar-putdown-diagnosis.md`](WORKLOG_20260905_DPI-01D1_ai-guitar-putdown-diagnosis.md) |
| 2026-09-05 | DPI-01D Track Loss Recovery Fix | FIX IMPLEMENTED / REAL REPLAY PARTIALLY CLOSED | Made cam03 Track-loss tolerance configurable and extended it to 16 rolling windows; real replay recovered the second electric-guitar cycle and emitted PUT_DOWN at 97.62s, while the AI吉他 EOF cycle remains correctly open. | [`WORKLOG_20260905_DPI-01D_tracking-loss-fix.md`](WORKLOG_20260905_DPI-01D_tracking-loss-fix.md) |
| 2026-09-05 | DPI-01D Full Dual-Guitar Behavior Replay | REPLAY EXECUTED / ACCEPTANCE BLOCKED | Reused the historical Offline Stage5/7C/8/9 GoldenMp4Analyzer chain in agents-py311; PTS coverage completed to EOF, the first electric-guitar cycle closed, but the second electric-guitar and black-guitar cycles remained incomplete before EOF, so no DPI-01D PASS was recorded. | [`WORKLOG_20260905_DPI-01D_full-behavior-replay.md`](WORKLOG_20260905_DPI-01D_full-behavior-replay.md) |
| 2026-09-05 | DPI-01C.3 Fixed Display Position Evidence Gate | IMPLEMENTED / REPLAY READINESS FAIL-CLOSED PENDING CURRENT MASK CONFIRMATION | Added explicit calibration and display-mode gates, four diagnostic failure reasons, and cam03 support-point evidence fields while preserving cam02 overlap semantics; Stage9 remains blocked until the current derived-video Mask confirmation is complete. | [`WORKLOG_20260905_DPI-01C3_fixed-display-position.md`](WORKLOG_20260905_DPI-01C3_fixed-display-position.md) |
| 2026-09-05 | DPI-01C.2 cam03 Action-Window Diagnostic | ACTION WINDOWS OBSERVED / SURFACE SEMANTICS REVIEW REQUIRED | Reused the bounded real propagation artifact to classify stationary, leaving, held, and returned windows for the white guitar, retained the black guitar as a stationary control, and recorded the expanded-surface conflict without changing Runtime behavior; full behavior replay remains pending. | [`WORKLOG_20260905_DPI-01C2_action-window-diagnostic.md`](WORKLOG_20260905_DPI-01C2_action-window-diagnostic.md) |
| 2026-09-04 | DPI-01C.2 cam03 Real Dual-Product Propagation | REAL PROPAGATION PASS / SURFACE SEMANTICS PENDING | Propagated 121 real frames in one SAM3.1 session with simultaneous `[0,2]` object retention, stable product-key mapping, and two unambiguous active-token windows; Surface semantics and full behaviour replay remain pending. | [`WORKLOG_20260904_DPI-01C2_real-propagation.md`](WORKLOG_20260904_DPI-01C2_real-propagation.md) |
| 2026-09-04 | DPI-01C.1 cam03 Two-Guitar Initialization | DPI-01C1 TWO-GUITAR INITIALIZATION PASS / SURFACE SCOPE DEFERRED | Produced and operator-confirmed same-frame, same-session real SAM3 Masks, created both Product Tracks atomically, and passed two bounded Runtime continuity windows; yellow robot, multi-surface scope, and full behavior replay remain deferred. | [`WORKLOG_20260904_DPI-01C1_two-guitar-real-probe.md`](WORKLOG_20260904_DPI-01C1_two-guitar-real-probe.md) |
| 2026-09-04 | DPI-01C.1 cam03 Configuration And Surface Candidate | REAL SINGLE-PROBE PASS / OPERATOR MASK CONFIRMATION PENDING | Verified the new source MP4, generated an unobstructed central-tabletop Surface Overlay, and completed one real SAM3 Probe for the right white guitar; Track creation remains blocked until the operator confirms the Mask. | [`WORKLOG_20260904_DPI-01C1_cam03-config-and-surface.md`](WORKLOG_20260904_DPI-01C1_cam03-config-and-surface.md) |
| 2026-09-04 | DPI-01B Offline Video Preflight | VIDEO PRECHECK PASS / REAL SAM3 ADAPTER BLOCKED | Preserved the original 2560x1440 MP4, created a 1280x720 H.264 copy, verified 3398/3398 decoded frames with monotonic PTS, and left real SAM3 initialization pending. | [`WORKLOG_20260904_DPI-01B_video-preflight.md`](WORKLOG_20260904_DPI-01B_video-preflight.md) |
| 2026-09-01 | DPI-01A Dense Product Initialization | CODE LOOP COMPLETE / UNIT GATE PASS / REAL EVIDENCE PENDING | Added fail-closed, operator-confirmed 3-5 Product first-frame initialization, exact PTS provenance, Runtime batch atomicity, config-driven keys, and an Offline tool; focused suite passes 167/167 while real SAM3/camera evidence remains pending. | [`WORKLOG_20260901_DPI-01A_dense-product-initialization.md`](WORKLOG_20260901_DPI-01A_dense-product-initialization.md) |
| 2026-08-31 | Staff Greeting Chinese Demo Overlay | COMPLETED / CHINESE VIDEO FINALIZED | Localized all system overlay labels, added `秒象科技` to every person box, passed 123/123 focused tests, and finalized a 1808-frame CUDA replay with unchanged Greeting results. | [`WORKLOG_20260831_staff-greeting-chinese-overlay.md`](WORKLOG_20260831_staff-greeting-chinese-overlay.md) |
| 2026-08-31 | Staff Greeting Demo Effect Validation P1.1 | COMPLETED / SUSTAINED-WINDOW CONFIDENCE VALIDATED | Replaced terminal lifetime-min confidence with final continuous qualifying-window minimum while retaining the 0.7 threshold; Cases A-F and 120/120 focused tests pass, and controlled CUDA replay naturally produces one confirmed Greeting. | [`WORKLOG_20260831_staff-greeting-confidence-semantics.md`](WORKLOG_20260831_staff-greeting-confidence-semantics.md) |
| 2026-08-31 | Staff Greeting Demo Effect Validation P1 | DIAGNOSIS COMPLETE / CONFIDENCE BLOCKER PROVEN | Built a real-Core per-PTS G1-G10 trace; all effective behavior gates pass, while lifetime-min person confidence falls to 0.518623 below the controlled-demo 0.7 terminal threshold. Provenance gates and deterministic confidence regressions were added without changing Official V1 semantics or thresholds. | [`WORKLOG_20260830_staff-greeting-offline-replay.md`](WORKLOG_20260830_staff-greeting-offline-replay.md) |
| 2026-08-31 | Staff Greeting Demo Effect Validation P0.1 | COMPLETED / P0 CLOSED | Added MP4-local subject truth at six PTS keyframes, fail-closed bbox/PTS matching independent of Track IDs, 18/18 subject assertions, deterministic Track23 replay, and 51/51 focused vision/Core/Offline/gate tests; tracking fragmentation remains known and no Greeting Core or threshold changed. | [`WORKLOG_20260830_staff-greeting-offline-replay.md`](WORKLOG_20260830_staff-greeting-offline-replay.md) |
| 2026-08-31 | Staff Greeting Demo Effect Validation P0 | COMPLETED / UPSTREAM FIX VALIDATED | Diagnosed Track23 false STAFF to merged person bbox plus vest reassignment, added fail-closed lower-torso association guard and Offline `imgsz=1280`, passed focused role/Core tests, and finalized CUDA replay with 1808/1808 frames and no Track23 role flip. | [`WORKLOG_20260830_staff-greeting-offline-replay.md`](WORKLOG_20260830_staff-greeting-offline-replay.md) |
| 2026-08-30 | Staff Greeting Offline Replay | IMPLEMENTED / REAL MP4 REPLAY FINALIZED | Offline artifact-only runner, verified PTS every-frame replay, 62/62 focused tests, 1433 decoded -> 1433 output frames, and final RTX 5070 Ti / CUDA replay; one cancelled cycle, no confirmed greeting, calibration provisional. | [`WORKLOG_20260830_staff-greeting-offline-replay.md`](WORKLOG_20260830_staff-greeting-offline-replay.md) |
| 2026-08-27 | MB-01A Offline Member Behavior | CODE LOOP COMPLETE / CONTROLLED POLICY REVIEW PASS / REAL EVIDENCE PENDING | Immutable run8 remains 0/2 BLOCKED under exact-single-tool policy; independent review under related read-only tool composition passes 2/2 without rerun. | [`WORKLOG_20260827_MB-01A_member-behavior.md`](WORKLOG_20260827_MB-01A_member-behavior.md) |
| 2026-08-26 | R5-04.2 Minimal Autonomous Answer Gate Closure | CODE COMPLETE / ACCEPTANCE BLOCKED | Isolated and bypassed the host Temp AF_UNIX failure, fixed checker/schema gaps, and retained immutable recovery runs; latest 3x7 still has autonomous presentation/protocol blockers. | [`WORKLOG_20260826_R5-04-2_minimal-autonomous-answer-gate.md`](WORKLOG_20260826_R5-04-2_minimal-autonomous-answer-gate.md) |
| 2026-08-26 | R5-04.1 Autonomous MCP Acceptance Semantic Integrity | COMPLETED / PRODUCTION BLOCKED | Added fail-closed product alias resolution, split grounding/presentation evidence, fixed terminal-answer lifecycle and checker binding; retained three real final-answer blockers. | [`WORKLOG_20260826_R5-04-1_autonomous-mcp-semantic-integrity.md`](WORKLOG_20260826_R5-04-1_autonomous-mcp-semantic-integrity.md) |
| 2026-08-26 | R5-04 follow-up observability and independent batches | COMPLETED / PRODUCTION CONDITIONAL | Stabilized Provider SSE/incomplete-stream errors, persisted per-round diagnostics and raw JSONL, and reran three independent batches at 7/7, 6/7, 7/7 (20/21). | [`WORKLOG_20260826_R5-04_followup-observability.md`](WORKLOG_20260826_R5-04_followup-observability.md) |
| 2026-08-26 | R5-04 Autonomous MCP Canary Reliability & Observability | COMPLETED / PRODUCTION CONDITIONAL | Fixed generic OpenAI-compatible stream termination, added sanitized diagnostics and bounded Canary verification; final autonomous evidence is 16/21 with five retained grounding failures. | [`WORKLOG_20260826_R5-04_autonomous-mcp-canary-reliability.md`](WORKLOG_20260826_R5-04_autonomous-mcp-canary-reliability.md) |
| 2026-08-26 | R5-03.2 Autonomous MCP Baseline Restoration | COMPLETED | Removed RetailVision-specific routing and answer ownership from the generic Java Agent; restored autonomous MCP selection and retained a truthful 19/21 observation baseline. | [`WORKLOG_20260826_R5-03-2_autonomous-mcp-baseline-restoration.md`](WORKLOG_20260826_R5-03-2_autonomous-mcp-baseline-restoration.md) |
| 2026-08-25 | R5-03.2.1 Deterministic Business Answer Stability Closure | REJECTED / SUPERSEDED | Historical 21/21 experiment rejected because Java fixed RetailVision routing and final answers; evidence retained but no longer qualifies the current architecture. | [`WORKLOG_20260825_R5-03-2-1_deterministic-business-answer-stability.md`](WORKLOG_20260825_R5-03-2-1_deterministic-business-answer-stability.md) |
| 2026-08-25 | R5-03.2 Controlled Business Answer Enrichment | BLOCKED | Split hard grounding from safe business interpretation; focused safety tests pass 75/75, but latest seven-question real-Agent smoke passed 5/7. | [`WORKLOG_20260825_R5-03-2_controlled-business-answer-enrichment.md`](WORKLOG_20260825_R5-03-2_controlled-business-answer-enrichment.md) |
| 2026-08-25 | R5-03.1.3 Agent grounding contract stabilization | COMPLETED | Unified MCP grounding instructions and seven tool descriptions; revalidated current evidence passed 7/7 with zero grounding/forbidden failures after score/comparison wording hardening. | [`WORKLOG_20260825_R5-03-1-3_agent-grounding-contract-stabilization.md`](WORKLOG_20260825_R5-03-1-3_agent-grounding-contract-stabilization.md) |
| 2026-08-25 | R5-03.1.2 Acceptance evidence integrity fix | BLOCKED | Closed negation, product/metric binding, non-overwrite, and test-summary integrity gaps; real Agent evidence remains 4/7 after 23 attempts. | [`WORKLOG_20260825_R5-03-1-2_acceptance-evidence-integrity.md`](WORKLOG_20260825_R5-03-1-2_acceptance-evidence-integrity.md) |
| 2026-08-25 | R5-03.1.1 Acceptance evidence closure | COMPLETED | Corrected unsupported engagement grounding, strict required-mode parsing, connected disabled-call error contract, and regenerated 7/7 real Agent evidence. | [`WORKLOG_20260825_R5-03-1-1_acceptance-evidence-closure.md`](WORKLOG_20260825_R5-03-1-1_acceptance-evidence-closure.md) |
| 2026-08-25 | R5-03.1 Canary readiness hardening | COMPLETED | Separated MCP queries from migration, isolated Java fixtures, stabilized errors/lifecycle, and captured sanitized seven-scenario Agent evidence. | [`WORKLOG_20260825_R5-03-1_canary-readiness-hardening.md`](WORKLOG_20260825_R5-03-1_canary-readiness-hardening.md) |
| 2026-08-25 | R5-03 Store Intelligence Agent limited integration | COMPLETED | Real mewcode-java seven-scenario MCP integration, trusted scope, failure handling, and single-store canary qualification completed. | [`WORKLOG_20260825_R5-03_store-intelligence-agent-integration.md`](WORKLOG_20260825_R5-03_store-intelligence-agent-integration.md) |
| 2026-08-25 | R5-02 Product Analytics controlled enablement | COMPLETED | Default-off projection/MCP controls, scoped query protection, audit, rollback, and limited-enablement qualification completed. | [`WORKLOG_20260825_R5-02_product-analytics-controlled-enablement.md`](WORKLOG_20260825_R5-02_product-analytics-controlled-enablement.md) |
| 2026-08-25 | R5-01 Product Analytics qualification | COMPLETED | Qualification evidence closed with corrected full-test counting and Event 804, migration, and MCP acceptance. | [`WORKLOG_20260825_R5-01_product-analytics-qualification.md`](WORKLOG_20260825_R5-01_product-analytics-qualification.md) |
| 2026-08-24 | Product Analytics V1 | COMPLETED | Anonymous analytics projection, idempotent writer, query metrics, MCP tools, and additive schema evolution completed. | [`WORKLOG_20260824_product-analytics-v1.md`](WORKLOG_20260824_product-analytics-v1.md) |
| 2026-08-24 | R4-08 governance | COMPLETED | Registry-first agent guidance and narrow generated-artifact ignore policy completed. | External archive `historical-docs/docs/repository/R4_08_GOVERNANCE_UPDATE_REPORT.md` |

## Authority Boundary

Worklogs are historical development records and decision context. They are
not:

- business contracts;
- architecture authority;
- implementation instructions that override current code and tests; or
- replacements for accepted ADRs.

When a worklog conflicts with another source, follow the authority order in
the Registry. Production runtime behavior and tests, accepted ADRs,
`CONTEXT.md`, current handoff, and current architecture documents remain the
authoritative sources for their respective domains.
