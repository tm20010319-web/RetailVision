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

Latest VLX entry: **Fixed-preset Windows video launcher**

Status: `IMPLEMENTED / SHORT REAL PIPELINE VERIFIED`

Double-click workflow selects a recording and existing object preset, runs the
existing pipeline, validates/exports facts and publishes a versioned ZIP/SHA256.
123 tests pass; a fresh 3-second three-toy smoke run completes all stages.
Full-length recognition accuracy remains unqualified; behavior rules unchanged.
See [`WORKLOG_20261029_vlx-fixed-video-launcher.md`](WORKLOG_20261029_vlx-fixed-video-launcher.md).

Latest all-merchandise video attempt: **Full-length candidate video / accuracy failed**

Status: `TEST VIDEO DELIVERED / ALL-PRODUCT ACTION COVERAGE NOT ACHIEVED`

Expanded VLX inventory and semantic SAM tracking covered the seven-minute
timeline but did not reliably recover all product actions. One incomplete
brown-plush pickup candidate, no confirmed putdown; true complete counts unknown.
See [`WORKLOG_20261028_all-merchandise-video-attempt.md`](WORKLOG_20261028_all-merchandise-video-attempt.md).

Latest entrance-camera replay: **Correct entrance view / merchandise-only split**

Status: `VIDEO VERIFIED / USER-CONFIRMED VIDEO-ONLY CUSTOMER COUNT 5`

The 91-second entrance video uses unchanged original entry rules with a local
doorway polygon. A subsequent user-confirmed role correction for blue-top
track 5 yields five customers, preserving the original four-count output and
all model weights. The seven-minute
video now has a separate merchandise-only delivery; goods accuracy remains
failed. See [`WORKLOG_20261028_entrance-camera-replay.md`](WORKLOG_20261028_entrance-camera-replay.md).

Latest seven-minute video test: **Home items and first entering group**

Status: `FILE DELIVERY COMPLETE / ENTRY COMPARISON VERIFIED / GOODS ACCURACY FAILED`

Fresh 420-second inference did not reliably localize the two home items. The
first group has five geometric crossings; the original customer filter counts
four because one person is labelled staff. Both outputs and empty goods facts
are preserved; 83 entry/export tests pass. See
[`WORKLOG_20261028_seven-minute-home-items-entry.md`](WORKLOG_20261028_seven-minute-home-items-entry.md).

Latest two-item video test: **Home monitor and AI eye massager**

Status: `VIDEO VERIFIED / ACTION ACCURACY FAILED`

Fresh VLX/pose analysis and isolated SAM follow-ups did not retain the two small
objects during pickup. Automatic counts remain 0/0; visual review shows each
item lifted once and carried out without a visible putdown. No reviewed counts
were inserted into model output. See
[`WORKLOG_20261028_vlx-home-items-video-test.md`](WORKLOG_20261028_vlx-home-items-video-test.md).

Latest three-toy behavior repair: **17:56:11 occlusion/release repair**

Status: `IMPLEMENTED / REPLAY VIDEO VERIFIED / USER REVIEW PENDING`

Fixed white false splits, orange delayed release and the missing early pink lift.
V5 records orange 1, white 1, pink 2 completed interactions, with an explicit
11-second inferred white occlusion. 108 tests pass; older 9/9 and 6/6 counts stay
intact. Completed VLX/SAM evidence is reused; fresh long-window V4 was stopped
under memory pressure. No database or delivery archive changes.
See [`WORKLOG_20261028_vlx-occlusion-repair.md`](WORKLOG_20261028_vlx-occlusion-repair.md).

Previous uploaded-video test: **17:56:11, three fixed toys**

Status: `TEST COMPLETE / VIDEO VERIFIED / BEHAVIOR ACCURACY FAILED`

Fresh VLX/pose evidence and continuous SAM3.1 tracking produced a full annotated
video. Its 4/4 counts contain false white-egg sessions, delayed orange release
and missed early pink motion; do not treat these as accepted facts. Startup
initialization and selected-target display fixes pass 76 tests. No database writes.
See [`WORKLOG_20261028_vlx-video-175611-test.md`](WORKLOG_20261028_vlx-video-175611-test.md).

Latest delivery: **RetailVision complete source snapshot**

Status: `DELIVERED / 176 VLX TESTS PASS / NO DATABASE ACCESS`

Delivered RetailVision full source with dependency sources, startup/config
examples, existing migrations and result-location notes. All 176 packaged VLX
tests pass; no database or runtime changes. Other projects are reported separately.
See [`WORKLOG_20261028_retailvision-full-source-handoff.md`](WORKLOG_20261028_retailvision-full-source-handoff.md).

Latest VLX handoff: **Isolated acceptance JSON and incremental source bundle**

Status: `DELIVERED / 81 TESTS PASS / NO DATABASE ACCESS`

Delivered existing file-only results, 15 sessions/45 anonymized relative-time
facts, field documentation, expected behaviors and incremental sources/tests.
Bundled-source tests and archive hashes pass. Database import and cloud integration
are deferred to the recipient's isolated acceptance workflow.
See [`WORKLOG_20261028_vlx-acceptance-handoff.md`](WORKLOG_20261028_vlx-acceptance-handoff.md).

Latest behavior repair: **Both 01:51:25 and 01:39:57 recordings**

Status: `IMPLEMENTED / TWO EXPORTS VERIFIED / USER REVIEW PENDING`

Both videos now show per-object totals alongside per-user/object counts. New
exports record 6/6 and 9/9 pickups/putdowns; 72 targeted tests and full video
decodes pass. VLX/pose evidence was reused, with fresh SAM3.1 mask tracking and
independent guitar validation. Timing remains a visual estimate; this is not a
general accuracy qualification. User review of the new exports is pending.
See [`WORKLOG_20261028_vlx-two-video-recovery.md`](WORKLOG_20261028_vlx-two-video-recovery.md).

Latest video display/test work: **Per-user/item counts and 01:51:25 recording**

Status: `DISPLAY VERIFIED / FULL TEST COMPLETE / ACCURACY FAILED / PREVIEW RESTORED`

User confirmed per-user/item cumulative counts and current holding time.
Only video presentation changed; current v6 recognition and rules are retained.
56 tests and recorded-trace visual checks passed. Fresh full-recording testing
on the 184.795s upload reports 2 pickups, 0 putdowns, both incomplete; four of
six clear source operations missed. Full/50s videos and new display verified;
preview restored to [1,2]. See
[`WORKLOG_20261028_vlx-user-item-overlay.md`](WORKLOG_20261028_vlx-user-item-overlay.md).

Previous uploaded-video test: **01:39:57 recording with fixed VLX v6**

Status: `FULL TEST COMPLETE / ACTION ACCURACY FAILED / PREVIEW RESTORED`

Fresh unchanged-v6 inference covered all 245.561 seconds (1,209 samples).
System output of 4 pickups/2 putdowns includes one false dog session; six of nine
clear visual pickup onsets are missed, with two incomplete records and delayed
release/ID errors. Full and 102s videos verified; preview restored to [1,2]. See
[`WORKLOG_20261028_vlx-video-013957-test.md`](WORKLOG_20261028_vlx-video-013957-test.md).

Latest behavior development: **VLX repeated handling and toss continuity**

Status: `IMPLEMENTED / TWO COMPLETED REPLAY SESSIONS / PREVIEW RESTORED`

User confirmed toss/catch remains one session. V6 adds isolated CSRT, track
ownership and supported-rest recovery. Final replay of this turn's fresh VLX
outputs reports two bee sessions (15.64s, 7.40s), no duplicate-label sessions;
147 tests pass and the native dual preview is restored. Only
`test_20261027_211259_v6_verified` is the final delivered artifact; raw fresh runs
and unsuccessful intermediate runs remain unchanged. Other objects/cameras and
precise physical release timing remain unqualified.
See [`WORKLOG_20261027_vlx-repeat-handling.md`](WORKLOG_20261027_vlx-repeat-handling.md).

Previous uploaded-video baseline: **21:12:59 recording with VLX v4**

Status: `REAL MODEL TEST COMPLETE / TWO BEE OPERATIONS MISSED / PREVIEW RESTORED`

The user chose a current-version test before further fixes. Fresh inference on
the 125.72s recording completed with unchanged v4: 613 samples, 0 action events.
Both visibly observed bee-plush operations were missed; fast-motion box loss and
unknown pickup origins prevent timers. Full video and 36s action clip decode
successfully. Dual preview was restored with [1, 2]; both feeds verified fresh.
See [`WORKLOG_20261027_vlx-video-211259-test.md`](WORKLOG_20261027_vlx-video-211259-test.md).

Latest uploaded-video follow-up: **VLX guitar holding timer**

Status: `REPORTED TIMERS FIXED / REGRESSION PASS / ACTION ACCURACY NOT ACCEPTED`

The two reported guitar screenshots now show 2.6s and 5.8s holding timers beside
the object. V4 uses shared-edge motion, per-hand evidence and recent resting-pose
recovery; 65 tests pass. Exact recorded-evidence replay exports a checked full
video and 26s example clip. Earlier guitar actions, release timing and instance
errors remain unresolved; the live preview stayed running.
See [`WORKLOG_20261027_vlx-guitar-timer.md`](WORKLOG_20261027_vlx-guitar-timer.md).

Latest uploaded-video behavior work: **VLX pickup recovery and new recording test**

Status: `IMPLEMENTED / REAL VIDEO TEST COMPLETE / ACTION ACCURACY NOT ACCEPTED`

Captured-trace regression now recovers a missed robot-dog pickup across short
tracking breaks, with same-wrist safeguards against a different-guitar switch.
Pose input size is 1280 after a real missing-person comparison; 57 focused tests
pass, including a guard against precontact box changes. The 19:16:36 recording
completed fresh inference plus a final v3 rule replay: 4 pickups, 3 putdowns,
18.40 seconds completed holding. Guitar misses, wrong-instance boxes, late
releases and anonymous-ID fragmentation remain; accuracy is not accepted.
Both videos decode successfully. The dual preview was restored with 小蜜蜂;
the GUI subsequently changed to [1, 2], which was preserved.
See [`WORKLOG_20261027_vlx-pickup-recovery.md`](WORKLOG_20261027_vlx-pickup-recovery.md).

Previous uploaded-video baseline: **VLX pickup / putdown backend**

Status: `IMPLEMENTED / TWO CAMERA VIDEO TESTS COMPLETE / ACTION ACCURACY NOT ACCEPTED`

Six fixed items (including the user-confirmed pink plush) can be analyzed through
an independent file runner using VLX grounding, wrist evidence and video-local
anonymous users. Exports action details, counts, holding times and annotated video.
52 focused tests pass; frontend and database work are deferred. The first user
recording produced a full annotated video and partial action results; visual
review found an orange-toy miss and a suspected guitar false pickup. The second
camera recording completed with zero events despite visible operations, plus
instance confusion and detection gaps; both videos were exported and the paused
dual-camera preview restored. See
[`WORKLOG_20261027_vlx-interaction-video.md`](WORKLOG_20261027_vlx-interaction-video.md).

Latest frontend authentication work: **VLX uses the existing Java login JWT**

Status: `LIVE / HTTP AND WSS VERIFIED / REAL JAVA LOGIN E2E PENDING`

The standalone 24-hour credential is retired. Item-search HTTP and WebSocket
now validate the original Java front JWT with verified uid session ownership.
92 focused tests and public HTTP/WSS/expiry/clear checks passed. Original Java
login remains the issuer; no new login or token exchange is added. See
[`WORKLOG_20261027_vlx-java-jwt.md`](WORKLOG_20261027_vlx-java-jwt.md).

Latest camera stability work: **VLX tracked annotation continuity**

Status: `LIVE / CURRENT-SCENE CONTINUITY CHECK PASSED / SEMANTIC IDENTITY OPEN`

Static flicker was reproduced from actual display/model traces. Candidate
matching, bounded missed-confirmation retention and label-swap rejection now
pass 54 focused tests. Dual tracking uses a measured 16-second confirmation
budget; original timestamps are preserved and held boxes say 跟踪中. Final
40-second observation captured no winner losses while matching/holding paths
were exercised; this does not qualify semantic identity or every frame. See
[`WORKLOG_20261027_vlx-stable-annotations.md`](WORKLOG_20261027_vlx-stable-annotations.md).

Previous frontend integration work: **VLX public API through Tailscale Funnel**

Status: `LIVE FUNNEL / HTTP AND WSS CHECKED / SCENE QUALIFICATION LIMITED`

Authenticated HTTP/WS service is running on loopback 8787 and publicly through
`desktop-qaer92d.tail173be1.ts.net`; start/publish/stop scripts and updated
frontend handoff are available. User enabled Funnel. 44 focused tests pass,
local known/generic searches and domain-based HTTP/WSS/clear were checked.
Its original separate-token handoff is superseded by the Java JWT entry above;
arbitrary-class/placement accuracy and an
independent developer-network connection remain unqualified. See
[`WORKLOG_20261023_vlx-public-api-funnel.md`](WORKLOG_20261023_vlx-public-api-funnel.md).

Previous frontend integration artifact: **VLX item-search API contract**

Status: `DOCUMENTS DELIVERED / API NOT IMPLEMENTED / NO PUBLIC EXPOSURE`

Chinese frontend contract, OpenAPI and saved-frame mocks are available under
`docs/api/vlx-item-search-v1/`, with a shareable ZIP. General-name recognition,
HTTP/WS, authenticated sessions and public deployment are future work, not
existing desktop runtime capabilities. See
[`WORKLOG_20261023_vlx-frontend-api-contract.md`](WORKLOG_20261023_vlx-frontend-api-contract.md).

Latest camera recognition work: **VLX separate large/small guitars**

Status: `LIVE / CURRENT PLACEMENT VERIFIED / GENERAL PLACEMENT OPEN`

User accepted distinct 大吉他 and 小吉他 labels, group aliases 吉他/电吉他,
and individual selection. Queries distinguish electric-guitar and ukulele
appearance instead of pixel size. 33 focused tests pass; current live camera-1
placement returned both in 9/9 frames with mean per-view updates 3.27--3.29s.
The old blurry horizontal crop still misses. See
[`WORKLOG_20261023_vlx-guitar-variants.md`](WORKLOG_20261023_vlx-guitar-variants.md).

Previous camera recognition work: **VLX missing-box correction**

Status: `DUPLICATE FILTER FIXED / GUITAR DISAMBIGUATION PARTIAL / LATENCY OPEN`

Actual overlapping proposals for one bee were incorrectly rejected as multiple
objects; golden regression and containment-aware merging fix that path. Guitar
semantic refinement helps but remains intermittent. Latest dual-target check
averaged 5.08--5.39s, above the requested ~3s; final cycles were around 4s.
See [`WORKLOG_20261023_vlx-missed-box-correction.md`](WORKLOG_20261023_vlx-missed-box-correction.md).

User selected behavior-detection-1 and behavior-detection-3, with one winning
view per selected target. Shared cached VLX/WeDetect serves both streams.
Full dual-target live test measured 3.44 seconds mean per-view confirmation;
view scores are quality indices, not recognition probabilities. See
[`WORKLOG_20261023_vlx-dual-view-selection.md`](WORKLOG_20261023_vlx-dual-view-selection.md).

User requests no annotation without input; initial names are 小蜜蜂 and 白色电吉他.
See [`WORKLOG_20261023_vlx-text-targets.md`](WORKLOG_20261023_vlx-text-targets.md).
User accepts realtime video/tracking between VLX confirmations. Existing YOLO
implementation is retained and official WeDetect proposals remain accepted.
See [`WORKLOG_20261023_vlx-realtime-tracking.md`](WORKLOG_20261023_vlx-realtime-tracking.md)
and the preceding [`VLX preview worklog`](WORKLOG_20261023_vlx-continuous-preview.md).
This standalone preview does not change Offline V1 or the prior YOLO configuration.

Latest recorded phase: **toy/guitar precision V4 correction and live validation**

Status: `WHITE-CLOTHING CORRECTION LIVE / REMAINING ACCURACY LIMITS / CONFIDENCE 0.50`

Current worklog:
[`WORKLOG_20261022_toy-guitar-precision-v4.md`](WORKLOG_20261022_toy-guitar-precision-v4.md)

User confirmed white clothing and a different yellow exhibit object are
distractors. Initial target recall recovered after reviewed positive/negative
supplementation, but the first candidate failed a full-camera garment check.
The context candidate passed these development regressions and was deployed;
actual live testing exposed an upright-guitar miss. The third candidate adds
reviewed original recording frames, preserves prior splits and 0.50, and
recovers the five upright guitar holdouts while retaining clothing/distractor
exclusions. Selected model is live: 261/261 fresh states contain both current
targets over 30 seconds at 8.68 FPS. The current guitar box slightly truncates
the headstock top. This is the separate YOLO preview; historical localization
errors and arbitrary placement/movement qualification limits remain explicit.
Subsequent movement at approximately 21:29:36 exposes a real hand-held guitar
miss and a chest-graphic bee false positive; correction has reopened using
the original recording. The earlier static result is not a motion pass.
The fourth candidate removes the observed chest-graphic false positives in
reviewed interleaved frames and improves hand-held recall, but a truncated
neck and one partly occluded hand-held miss remain. It is not deployed.
The fifth run adds 29 reviewed training-only scale views and passes the original
102 test entries at selected 960 inference with a same-frame guitar-only 640
retry. The retry recovers a real lamp-occluded guitar live failure. Actual
22:26 static observation contains both true items in 244/244 fresh states;
full motion replay still has seven graphic-as-bee false frames. The new
22:19:59 white-clothing event is reproduced from original pixels: false guitar
0.76 versus real guitar 0.54, and four pinned regression frames all fail.
A sixth candidate adds 21 reviewed full/context views and trains 100 epochs.
It is rejected as a standalone replacement due to old-scene false boxes. The
final live policy retains the scale primary and uses the sixth checkpoint to
verify same-frame guitar candidates one-to-one at IoU 0.40. White-clothing
false-guitar frames improve from 50 to 0 among 424 sampled frames, retaining the
true guitar in all 424. Original 102 test predictions stay identical; four new
tests retain one existing bee miss. PID 70528 is live after a 245/245 static
observation at 8.11 FPS. Seven motion-replay bee false frames and occlusion /
localization limits remain; the overall accuracy goal is open.

Latest resource preparation: **toy/guitar web negative samples**

Status: `COLLECTION COMPLETED / NOT TRAINED / LIVE UNCHANGED`

User requested direct download and screening of online negatives for the same
camera. Selected 168 attributed Commons photos (78 white clothing, 61 yellow
objects, 29 white objects) with empty YOLO labels are isolated in
`data/toy_guitar_web_negatives_20261022/`. This is training-resource preparation,
not accuracy qualification; existing live weights, 0.50 and original splits
remain in place. See
[`WORKLOG_20261022_toy-guitar-web-negatives.md`](WORKLOG_20261022_toy-guitar-web-negatives.md).

Previous recorded phase: **toy/guitar relocation V3 training and live validation**

Status: `COMPLETED / LIVE CONFIDENCE 0.50 / ACCURACY LIMITATIONS REMAIN`

Current worklog:
[`WORKLOG_20261022_toy-guitar-relocation-v3.md`](WORKLOG_20261022_toy-guitar-relocation-v3.md)

User authorized supplementing the newly supplied video and validating live
effects. The selected chair-pose model preserves V2 splits and detects both
items in 23 new temporal test frames. At confidence 0.25, a 30-second live
observation has both classes present in 255/255 states at 8.48 FPS, with one
extra guitar box. After the user reported confusing-object false positives,
live confidence was raised to 0.50 at 18:19; the following 88 fresh states
showed no detections. This is score filtering, not an accuracy qualification.
The AI replay and visual reviews are complete. Old guitar localization errors
and a transient camera-input failure remain explicit. This preview uses YOLO,
not VLX; arbitrary placement and all-motion accuracy are not established.

Previous recorded phase: **VLX zero-shot grounding feasibility trial**

Status: `IN PROGRESS / MODEL DOWNLOAD / NO VLX INFERENCE RESULT YET`

Current worklog:
[`WORKLOG_20261022_vlx-seek-trial.md`](WORKLOG_20261022_vlx-seek-trial.md)

User confirmed a names-only localization/tracking/recording goal. VLX-Seek 1.5
10B and the automatic WeDetect proposal path are being tested separately;
Windows imports and NF4 CUDA computation pass. Actual proposal generation has
run on four cases; full model download/inference and live acceptance are pending.
The original SAM3 integration has a separate verified short-camera probe,
documented in [`WORKLOG_20261022_toy-guitar-original-pipeline.md`](WORKLOG_20261022_toy-guitar-original-pipeline.md),
and has not been switched into a continuously running accepted viewer.

Previous recorded phase: **toy/guitar V2 live camera test**

Status: `LIVE PREVIEW RUNNING / USER PLACEMENT TEST PENDING`

Current worklog:
[`WORKLOG_20261021_toy-guitar-v2-live-test.md`](WORKLOG_20261021_toy-guitar-v2-live-test.md)

User requested connection for testing. A separate V2 live configuration starts
the verified behavior-detection-1 camera preview with the multi-pose checkpoint.
Fresh frames show both targets at approximately 9 FPS. V1 configurations remain
intact; user movement/placement testing is pending.

Previous recorded phase: **toy/guitar multi-pose YOLO V2**

Status: `COMPLETED / PINNED MISS RECOVERED / LIVE MODEL UNCHANGED`

Current worklog:
[`WORKLOG_20261021_toy-guitar-multipose-v2.md`](WORKLOG_20261021_toy-guitar-multipose-v2.md)

New 3-second sampling yields 121 labeled images (242 boxes). The independent
V2 experiment combines 323 images while preserving all V1 split assignments.
Training completed 75 epochs. The pinned miss now detects the bee at 0.9398;
new-video tests find all 40 targets but retain 3 duplicate boxes. A separately
audited old missing label exposes one loose guitar localization. Live model
and original staff-vest/V1 configurations remain unchanged.

Previous recorded phase: **bee placement/pose detection miss diagnosis**

Status: `MISS REPRODUCED / NO FIX DEPLOYED`

Current worklog:
[`WORKLOG_20261021_bee-pose-detection-diagnosis.md`](WORKLOG_20261021_bee-pose-detection-diagnosis.md)

A user-confirmed disappearing bee box is reproduced on a pinned source frame.
Original-pose confidence is 0.9847; changed live-pose candidates are below 0.04.
Higher input resolution does not restore detection. The preview uses per-frame
prediction without persistent tracking. No live model/configuration changed.

Previous recorded phase: **behavior-detection-1 toy/guitar live preview**

Status: `LIVE PREVIEW RUNNING / RELOCATION NOT VERIFIED`

Current worklog:
[`WORKLOG_20261021_toy-guitar-camera1-live.md`](WORKLOG_20261021_toy-guitar-camera1-live.md)

The selected EasyVMS camera `行为检测-1` is verified at `192.0.2.6` by its
P2P identity. The standalone YOLO11n live window is running with explicit
yellow-bee-toy and white-guitar Chinese labels, about 9 FPS in actual IR video.
Four focused tests and the real probe pass. Current-frame counts are shown;
relocation and instance re-identification remain unverified.

Previous recorded phase: **toy/guitar independent YOLO training**

Status: `SINGLE-VIDEO BASELINE TRAINED / RELOCATION NOT VERIFIED`

Current worklog:
[`WORKLOG_20261021_toy-guitar-yolo-training.md`](WORKLOG_20261021_toy-guitar-yolo-training.md)

Separate two-class YOLO11n configuration/entry point preserves the staff-vest
workflow. The current 202-image annotation package passes validation and five
focused tests. GPU training completed at 38 epochs with early stopping; all 29
held-out images have matching predicted class counts at confidence 0.25.
Evaluation is limited to the same video; relocation remains unverified.

Previous recorded phase: **product location environment restoration**

Status: `CAMERA IDENTITY VERIFIED / SAM3 FRAME PROPOSAL READY / LIVE QUERY NOT IMPLEMENTED`

Current worklog:
[`WORKLOG_20261021_product-location-environment-restore.md`](WORKLOG_20261021_product-location-environment-restore.md)

The Windows main/SAM3 environments and bundled weights are restored under
`D:\RetailVisionDev`; import, checkpoint, synthetic YOLO and CUDA checks pass,
along with 60 focused initialization/tracking/adapter tests. The selected
EasyVMS device's DID is verified at `192.0.2.4`; admin/empty-password access
and a real snapshot work. A saved-frame SAM3 guitar proposal awaits operator
confirmation. Continuous stream, region calibration, the new query module and
real-camera acceptance remain unfinished.

Previous recorded phase: **cam03 demo original-PTS rendering fix**

Status: `VIDEO FIX VERIFIED / SOURCE AND OLD OUTPUT PRESERVED`

Previous worklog:
[`WORKLOG_20260908_cam03-demo-pts-render-fix.md`](WORKLOG_20260908_cam03-demo-pts-render-fix.md)

The screenshot-reported person-box drift was traced to decoder frame insertion
before original-PTS indexing. The main-checkout renderer now preserves all 4745
original frames, verifies every one of 4859 output mappings, and produces a
separate 194.36-second corrected MP4. The real FFmpeg regression and encoded
pixel comparisons pass. Existing confirmed identity and six manually imported
events are retained; no database writes occurred in this rendering fix. Agent
answer evidence and import-script idempotency are not closed by this work.

Previous identity closure:
[`WORKLOG_20260907_cam03-formal-profile-geometry-closure.md`](WORKLOG_20260907_cam03-formal-profile-geometry-closure.md)

Previous receiver startup and threshold change:
[`WORKLOG_20260907_cam03-live-threshold-060.md`](WORKLOG_20260907_cam03-live-threshold-060.md)

The user explicitly requested 0.6 for the current receiver. Restart succeeded
with effective threshold 0.6 and persisted new cam03 heartbeats. Default 0.75,
old experiments and formal identity gates remain unchanged. Face preflight was
pending at that earlier check. The restart interruption remains visible in Coverage evidence.

Previous preparation (status below describes the earlier pre-start check):
[`WORKLOG_20260907_cam03-onsite-preparation.md`](WORKLOG_20260907_cam03-onsite-preparation.md)

Identity no longer hardcodes a verified Presentation timeline; new capture saves
actual source-frame duration/PTS evidence and existing rules determine status.
Three current member histories resolve. Live Push is not ready: 8081 was not
listening and automated startup was rejected by execution policy. A scoped
existing-receiver launcher is prepared. No new face/action sample or Run has
executed and no formal behavior writes occurred.

Previous integration:
[`WORKLOG_20260907_cam03-cli-identity-integration.md`](WORKLOG_20260907_cam03-cli-identity-integration.md)

The new saved-Run CLI executed the real durable-inbox query and existing identity
chain with 844 same-Run body observations and six own behavior facts. Three
diagnostic overlays broadly align the face with Track 2; both Tracks remain
unmatched at 0.75 (zero confirmed member events). Profile qualification and an
observed OSD/PTS clock discrepancy remain separate from CLI functionality. No
threshold, algorithm or business database write was changed.

Previous profile/configuration work:
[`WORKLOG_20260907_cam03-profile-reuse-audit.md`](WORKLOG_20260907_cam03-profile-reuse-audit.md)

Readiness now loads the configured full profile instead of resolving an empty
collection. Indexed archived calibration is verified for cam02, but rejects the
cam03 physical serial. Existing identity/geometry algorithms are reused;
applicable cam03 calibration evidence has not been located. See the worklog for
the difference between configuration loading and qualification.

Previous readiness work:
[`WORKLOG_20260907_cam03-identity-evidence-readiness.md`](WORKLOG_20260907_cam03-identity-evidence-readiness.md)

Three verified current cam03 device-member mappings now have revisions effective
September 7, not backdated. Sync history wiring and independent-run Person
evidence export pass tests. The old six-event sample remains anonymous; real
profile, geometry, scope registration and new-sample verification remain open.
The 0.75/0.65 experiment is preserved in
[`WORKLOG_20260907_cam03-threshold-experiment.md`](WORKLOG_20260907_cam03-threshold-experiment.md).

Previous phase:
[`WORKLOG_20260906_cam03-video-member-validation.md`](WORKLOG_20260906_cam03-video-member-validation.md)

The new cam03 video required only a source-bound tight prompt bbox for the
second guitar. Existing Stage5/7C/8/9 replay produced six behavior facts. The
cam03 Recognition window has complete Push Coverage but one ignored, unmapped
record; the capability profile remains unresolved, so no confirmed member
behavior was published.

Previous phase:
[`WORKLOG_20260906_DPI-01E2_real-recognition-wiring.md`](WORKLOG_20260906_DPI-01E2_real-recognition-wiring.md)

The cam03 mapping is loaded through the normal configuration path and the
read-only historical Coverage audit ran. The cam03 capability profile is
absent and the frozen interval has no provable Push end heartbeat, so no real
identity query or database write was performed.

Previous phase:
[`WORKLOG_20260831_staff-greeting-chinese-overlay.md`](WORKLOG_20260831_staff-greeting-chinese-overlay.md)

Previous phase summary:

- Offline analyzed-video system labels are Chinese, and every person detection
  box carries the `秒象科技` brand label.
- The formal Offline CLI defaults to Chinese presentation while reusable
  renderer classes retain explicit English compatibility.
- CUDA run `05ebc946c163467fb56090fb58179e9d` finalized with verified PTS,
  `1808/1808` frames, and the unchanged P1.1 result of one confirmed Greeting.
- The combined focused suite passes `123/123`.

Preceding worklog:
[`WORKLOG_20260831_staff-greeting-confidence-semantics.md`](WORKLOG_20260831_staff-greeting-confidence-semantics.md)

The preceding phase worklog remains available at
[`WORKLOG_20260826_R5-04-1_autonomous-mcp-semantic-integrity.md`](WORKLOG_20260826_R5-04-1_autonomous-mcp-semantic-integrity.md).

Earlier worklogs remain indexed here for continuity and traceability. Only the
latest worklog is required for startup; older entries remain available until a
separate owner-approved worklog compaction decision.

## Worklog Entries

| date | phase/topic | status | summary | worklog |
| --- | --- | --- | --- | --- |
| 2026-10-29 | VLX fixed video launcher | IMPLEMENTED / SHORT PIPELINE VERIFIED | Three fixed presets; interactive Windows entry, stage logs, export and verified ZIP/SHA256; 123 tests; fresh 3-second smoke passes; rules unchanged. | [`WORKLOG_20261029_vlx-fixed-video-launcher.md`](WORKLOG_20261029_vlx-fixed-video-launcher.md) |
| 2026-10-28 | All-merchandise video attempt | TEST VIDEO / ACCURACY FAILED | Full 420s candidate video; 29 VLX inventory frames, nine SAM target attempts; one incomplete pickup; all-action coverage not achieved. | [`WORKLOG_20261028_all-merchandise-video-attempt.md`](WORKLOG_20261028_all-merchandise-video-attempt.md) |
| 2026-10-28 | Entrance camera replay | VIDEO VERIFIED / ROLE FILTER DIFFERENCE | Correct 91s view; original customer count 4 vs 5 people crossing; goods-only artifacts separate, accuracy failed. | [`WORKLOG_20261028_entrance-camera-replay.md`](WORKLOG_20261028_entrance-camera-replay.md) |
| 2026-10-28 | VLX occlusion/release repair | IMPLEMENTED / USER REVIEW PENDING | V5: orange 1, white 1, pink 2; occlusion explicit; 108 tests; old 9/9 and 6/6 retained; no database writes. | [`WORKLOG_20261028_vlx-occlusion-repair.md`](WORKLOG_20261028_vlx-occlusion-repair.md) |
| 2026-10-28 | Seven-minute home items / entry | FILE DELIVERY / GOODS ACCURACY FAILED | 1849 fresh samples; goods unreliable; five first-group crossings versus four with original staff filter; relative facts exported; 83 tests. | [`WORKLOG_20261028_seven-minute-home-items-entry.md`](WORKLOG_20261028_seven-minute-home-items-entry.md) |
| 2026-10-28 | VLX home monitor / eye massager | VIDEO VERIFIED / ACCURACY FAILED | Full fresh VLX run missed both pickups; SAM follow-ups also lost targets; 72 tests and video decodes pass; no manual event insertion. | [`WORKLOG_20261028_vlx-home-items-video-test.md`](WORKLOG_20261028_vlx-home-items-video-test.md) |
| 2026-10-28 | VLX 17:56:11 three-object test | VIDEO VERIFIED / ACCURACY FAILED | Full VLX + SAM video; 4/4 automated counts include false white sessions and late orange release; 76 tests pass; no DB or delivery archive changes. | [`WORKLOG_20261028_vlx-video-175611-test.md`](WORKLOG_20261028_vlx-video-175611-test.md) |
| 2026-10-28 | RetailVision full source handoff | COMPLETED / USER MERGE PENDING | Full source and dependency snapshots; startup/config/migration instructions; 176 VLX tests pass; no DB access; other projects reported separately. | [`WORKLOG_20261028_retailvision-full-source-handoff.md`](WORKLOG_20261028_retailvision-full-source-handoff.md) |
| 2026-10-28 | VLX isolated acceptance handoff | COMPLETED / USER ACCEPTANCE PENDING | Incremental source and sanitized JSON: 15 sessions, 45 facts, 179.778s holding; 81 packaged tests pass; no database access or cloud writes. | [`WORKLOG_20261028_vlx-acceptance-handoff.md`](WORKLOG_20261028_vlx-acceptance-handoff.md) |
| 2026-10-28 | VLX user/item video statistics | DISPLAY VERIFIED / ACCURACY FAILED | Per-user/item counts and timers; 56 tests; fresh 184.795s run records 2 incomplete pickups, misses four other operations; videos verified and preview restored. | [`WORKLOG_20261028_vlx-user-item-overlay.md`](WORKLOG_20261028_vlx-user-item-overlay.md) |
| 2026-10-28 | VLX 01:39:57 video test | TEST COMPLETE / ACCURACY FAILED | Full fresh v6: 4 pickups/2 putdowns, including 1 false session; six clear subsequent pickups missed; both videos verified and preview restored. | [`WORKLOG_20261028_vlx-video-013957-test.md`](WORKLOG_20261028_vlx-video-013957-test.md) |
| 2026-10-28 | VLX repeated handling | IMPLEMENTED / REPLAY PASS | Final fresh-evidence replay: two bee sessions, 15.64s + 7.40s; 147 tests; videos verified and preview restored. Broader accuracy not accepted. | [`WORKLOG_20261027_vlx-repeat-handling.md`](WORKLOG_20261027_vlx-repeat-handling.md) |
| 2026-10-27 | VLX 21:12:59 video test | TEST COMPLETE / ACTION FAILURE | Unchanged v4 fresh inference: 613 samples, 0 events; two visual bee operations missed; full/36s clip verified; preview restored. | [`WORKLOG_20261027_vlx-video-211259-test.md`](WORKLOG_20261027_vlx-video-211259-test.md) |
| 2026-10-27 | VLX guitar timer | REPORTED TIMERS FIXED / ACCURACY NOT ACCEPTED | 65 tests; two reported screenshots show 2.6s / 5.8s; full v4 rule replay and 26s example clip verified; earlier misses and release errors remain. | [`WORKLOG_20261027_vlx-guitar-timer.md`](WORKLOG_20261027_vlx-guitar-timer.md) |
| 2026-10-27 | VLX pickup recovery | IMPLEMENTED / REAL TEST COMPLETE / ACCURACY NOT ACCEPTED | 57 tests; short-break/same-wrist/postcontact safeguards; pose 1280; final v3 replay: 4 pickups / 3 putdowns / 18.40s; remaining misses and wrong instances; preview restored. | [`WORKLOG_20261027_vlx-pickup-recovery.md`](WORKLOG_20261027_vlx-pickup-recovery.md) |
| 2026-10-27 | VLX video interaction backend | IMPLEMENTED / REAL ACTION ACCURACY NOT ACCEPTED | 52 focused tests; two camera recordings exported. First has misses/false-pickup concern; second emits zero events despite visible operations. Raw evidence preserved; paused dual preview restored. | [`WORKLOG_20261027_vlx-interaction-video.md`](WORKLOG_20261027_vlx-interaction-video.md) |
| 2026-10-27 | VLX Java login JWT | LIVE / REAL LOGIN E2E PENDING | Existing Java front JWT replaces standalone tokens; 92 tests and public HTTP/WSS/owner/expiry/clear verified. | [`WORKLOG_20261027_vlx-java-jwt.md`](WORKLOG_20261027_vlx-java-jwt.md) |
| 2026-10-27 | VLX annotation continuity | LIVE / SEMANTIC IDENTITY OPEN | Reproduced static flicker; bounded retention, candidate matching and isolated rechecks; 54 tests, final 40s check has zero sampled losses; physical identity remains open. | [`WORKLOG_20261027_vlx-stable-annotations.md`](WORKLOG_20261027_vlx-stable-annotations.md) |
| 2026-10-23 | VLX public API and Funnel | LIVE / SCENE QUALIFICATION LIMITED | Authenticated sessions, synchronized JPEG/boxes, general unverified queries; 44 tests and domain HTTP/WSS/clear checked; real URLs and separate credential handoff. | [`WORKLOG_20261023_vlx-public-api-funnel.md`](WORKLOG_20261023_vlx-public-api-funnel.md) |
| 2026-10-23 | VLX frontend API contract | DOCUMENTS DELIVERED / API NOT IMPLEMENTED | Chinese search/box contract, OpenAPI and same-frame JPEG mocks packaged; no public service deployed; general queries remain future work. | [`WORKLOG_20261023_vlx-frontend-api-contract.md`](WORKLOG_20261023_vlx-frontend-api-contract.md) |
| 2026-10-23 | VLX separate guitar variants | LIVE / GENERAL PLACEMENT OPEN | Large/small appearance categories and group/single input; 33 focused tests; current placement 9/9 both, mean update ~3.28s; old blurry crop still misses. | [`WORKLOG_20261023_vlx-guitar-variants.md`](WORKLOG_20261023_vlx-guitar-variants.md) |
| 2026-10-23 | VLX missing-box correction | FILTER FIXED / ACCURACY AND LATENCY OPEN | Actual bee duplicate-regression fixed; precise guitar refinement partial; 30 focused tests pass; mean dual-target update exceeds 3s. | [`WORKLOG_20261023_vlx-missed-box-correction.md`](WORKLOG_20261023_vlx-missed-box-correction.md) |
| 2026-10-23 | VLX dual-camera view selection | LIVE / ACCURACY OPEN | Two verified cameras, one winner per target; 25 focused tests pass; mean dual-target confirmation 3.44s; physical-instance identity unqualified. | [`WORKLOG_20261023_vlx-dual-view-selection.md`](WORKLOG_20261023_vlx-dual-view-selection.md) |
| 2026-10-23 | VLX text-selected annotations | LIVE | Default empty input, Chinese controls and revision-safe clearing; real per-target replays verified. | [`WORKLOG_20261023_vlx-text-targets.md`](WORKLOG_20261023_vlx-text-targets.md) |
| 2026-10-23 | VLX realtime video and tracking | LIVE / MOTION ACCURACY OPEN | Live video independent of VLX; delayed result replay and optical-flow tracking verified by focused tests. | [`WORKLOG_20261023_vlx-realtime-tracking.md`](WORKLOG_20261023_vlx-realtime-tracking.md) |
| 2026-10-23 | Continuous VLX preview | IMPLEMENTED | Three provided screenshots replayed; sampled display superseded by realtime tracking and text controls. | [`WORKLOG_20261023_vlx-continuous-preview.md`](WORKLOG_20261023_vlx-continuous-preview.md) |
| 2026-10-22 | Toy/guitar web negatives | COLLECTION COMPLETED | 168 reviewed online negatives with empty labels and per-image attribution; no training, deployment or split changes. | [`WORKLOG_20261022_toy-guitar-web-negatives.md`](WORKLOG_20261022_toy-guitar-web-negatives.md) |
| 2026-10-22 | Toy/guitar precision V4 | IN PROGRESS | Third candidate passes recorded exclusions and a static 30-second interval; later hand-held guitar miss and garment-graphic bee false positive reopen correction at fixed 0.50. | [`WORKLOG_20261022_toy-guitar-precision-v4.md`](WORKLOG_20261022_toy-guitar-precision-v4.md) |
| 2026-10-22 | Toy/guitar relocation V3 | COMPLETED | Selected chair checkpoint; retained 0.25 evaluation and failures; user raised live confidence to 0.50, followed by 88 fresh states with no detections; accuracy limitations remain. | [`WORKLOG_20261022_toy-guitar-relocation-v3.md`](WORKLOG_20261022_toy-guitar-relocation-v3.md) |
| 2026-10-22 | VLX zero-shot grounding trial | IN PROGRESS | Official Seek code/checkpoint pinned; isolated NF4/SDPA environment passes CUDA check; automatic proposals run, full inference pending. | [`WORKLOG_20261022_vlx-seek-trial.md`](WORKLOG_20261022_vlx-seek-trial.md) |
| 2026-10-22 | Toy/guitar original SAM3 pipeline | SHORT PROBE VERIFIED | Original SAM3/Stage10 produces 28 tracked frames across 3 windows and 3 decodable recordings; continuous switch not completed before VLX trial. | [`WORKLOG_20261022_toy-guitar-original-pipeline.md`](WORKLOG_20261022_toy-guitar-original-pipeline.md) |
| 2026-10-21 | Toy/guitar V2 live camera test | LIVE RUNNING / TEST PENDING | User-authorized camera-1 preview uses V2; verified identity and fresh frames show both targets at about 9 FPS; separate configuration preserves V1. | [`WORKLOG_20261021_toy-guitar-v2-live-test.md`](WORKLOG_20261021_toy-guitar-v2-live-test.md) |
| 2026-10-21 | Toy/guitar multi-pose YOLO V2 | COMPLETED / LIVE UNCHANGED | 121 new labeled images; V2 trained 75 epochs, pinned bee miss restored at 0.9398; new-video 40/40 targets matched with 3 duplicates; old missing-label audit retained. | [`WORKLOG_20261021_toy-guitar-multipose-v2.md`](WORKLOG_20261021_toy-guitar-multipose-v2.md) |
| 2026-10-21 | Bee placement/pose detection miss diagnosis | DIAGNOSED / NOT FIXED | Pinned live-frame replay misses visible bee twice; score below 0.04 after pose change; input 1280 does not resolve it; per-frame preview has no persistent tracker. | [`WORKLOG_20261021_bee-pose-detection-diagnosis.md`](WORKLOG_20261021_bee-pose-detection-diagnosis.md) |
| 2026-10-21 | Behavior-detection-1 toy/guitar live preview | LIVE RUNNING | Camera-1 P2P identity verified at 192.0.2.6; standalone Chinese YOLO preview recognizes both targets at about 9 FPS in IR video; four tests pass; relocation not verified. | [`WORKLOG_20261021_toy-guitar-camera1-live.md`](WORKLOG_20261021_toy-guitar-camera1-live.md) |
| 2026-10-21 | Toy/guitar independent YOLO training | COMPLETED | Independent YOLO11n baseline trained for 38 epochs; five focused tests pass, 29 held-out predictions match class counts, original staff-vest files unchanged; relocation not verified. | [`WORKLOG_20261021_toy-guitar-yolo-training.md`](WORKLOG_20261021_toy-guitar-yolo-training.md) |
| 2026-10-21 | Product location environment restoration | CAMERA IDENTITY VERIFIED / SAM3 FRAME PROPOSAL READY | Environments and 60 focused tests pass. Camera DID, admin/empty-password access and real snapshot verified; SAM3 guitar proposal awaits confirmation. Continuous query and onsite acceptance pending. | [`WORKLOG_20261021_product-location-environment-restore.md`](WORKLOG_20261021_product-location-environment-restore.md) |
| 2026-09-08 | cam03 demo original-PTS rendering fix | VIDEO FIX VERIFIED / ORIGINALS PRESERVED | Fixed FFmpeg decoder frame insertion; 4745 original frames and all 4859 output mappings verified, both reported drift cases pass encoded-pixel comparison, Chinese labels and source audio retained, zero database writes. | [`WORKLOG_20260908_cam03-demo-pts-render-fix.md`](WORKLOG_20260908_cam03-demo-pts-render-fix.md) |
| 2026-09-07 | cam03 formal profile and geometry closure | GEOMETRY VERIFIED / PROFILE CONFIGURED / TRACK 1 CONFIRMED / NO FORMAL BEHAVIOR WRITES | Refuted the 8-pixel padding candidate with SIFT/RANSAC (1458 inliers, 0.30 px median) between the accepted Recognition's own BackgroundImage and the same-Run PTS-22s recording frame, generated the hash-bound cam03 profile from cam03's real push fingerprint, and re-ran the identity stage formally at 0.75: Track 1 confirmed member-gaojunjie with 6 confirmed facts, publication_eligible false, zero formal writes. | [`WORKLOG_20260907_cam03-formal-profile-geometry-closure.md`](WORKLOG_20260907_cam03-formal-profile-geometry-closure.md) |
| 2026-09-07 | cam03 handoff state verification | DIAGNOSTIC IDENTITY REVERIFIED / FORMAL PROFILE-GEOMETRY BLOCKED | Read-only session: confirmed the timeline-status fix is already in the workspace, found no 8081 receiver running, and independently re-ran the identity CLI on the fixed Run at 0.75 reproducing one accepted record, Track 1 probable, zero confirmed facts. | [`WORKLOG_20260907_cam03-handoff-state-verification.md`](WORKLOG_20260907_cam03-handoff-state-verification.md) |
| 2026-09-07 | cam03 formal identity review | IDENTITY-ONLY RECHECK PASS / FORMAL PROFILE-GEOMETRY BLOCKED | Reused the fixed Run's person snapshots and six behavior facts; formal 0.75 Recognition recheck accepted one record, but missing cam03 profile and unverified geometry keep Track 1 probable and confirmed count at zero. | [`WORKLOG_20260907_cam03-formal-identity-review.md`](WORKLOG_20260907_cam03-formal-identity-review.md) |
| 2026-09-07 | cam03 new action diagnostic run | BEHAVIOR REPLAY PASS / FORMAL MEMBER BINDING BLOCKED | Independent source-bound Run emitted six behavior facts; diagnostic Recognition and visual association ran, but the behavior Track remains probable with zero formal writes because cam03 profile/geometry are unqualified. | [`WORKLOG_20260907_cam03-new-action-diagnostic-run.md`](WORKLOG_20260907_cam03-new-action-diagnostic-run.md) |
| 2026-09-07 | cam03 fresh face preflight | ACCEPTANCE PASS / HISTORY RESOLVED / VIDEO PENDING | Two new accepted events with Event-Time mapping; existing diagnostic Provider confirms the latest bounded Coverage window; new action Run not yet executed. | [`WORKLOG_20260907_cam03-face-preflight.md`](WORKLOG_20260907_cam03-face-preflight.md) |
| 2026-09-07 | cam03 live receiver threshold 0.60 | RECEIVER RUNNING / FACE PREFLIGHT PENDING | Explicit user-requested 0.6 process override, successful restart and real heartbeat persistence; 22 tests passed, no formal behavior writes. | [`WORKLOG_20260907_cam03-live-threshold-060.md`](WORKLOG_20260907_cam03-live-threshold-060.md) |
| 2026-09-07 | cam03 onsite single-member preparation | TIMELINE FIXED / RECEIVER START REQUIRED | Removed fabricated verified timeline, saved actual frame durations, confirmed current device/history mappings; foreground receiver prepared, new onsite Recognition/Run pending. | [`WORKLOG_20260907_cam03-onsite-preparation.md`](WORKLOG_20260907_cam03-onsite-preparation.md) |
| 2026-09-07 | cam03 minimal CLI identity integration | REAL DIAGNOSTIC PASS / NO MEMBER MATCH | Existing Provider/Guarded association/projector connected to one new Run; three coarse-aligned overlays, six anonymous events, zero writes; 199 tests + 22 subtests. | [`WORKLOG_20260907_cam03-cli-identity-integration.md`](WORKLOG_20260907_cam03-cli-identity-integration.md) |
| 2026-09-07 | cam03 profile and calibration reuse audit | LOADER FIXED / APPLICABLE EVIDENCE NOT LOCATED | Fixed empty profile loading and Runtime geometry wiring; indexed real cam02 evidence is serial-scoped, not directly reusable for cam03. | [`WORKLOG_20260907_cam03-profile-reuse-audit.md`](WORKLOG_20260907_cam03-profile-reuse-audit.md) |
| 2026-09-07 | cam03 identity evidence readiness | REAL VALIDATION BLOCKED | Current-time member revisions verified and applied; synchronization history and same-run observation export tested; old six events anonymous, zero confirmed. | [`WORKLOG_20260907_cam03-identity-evidence-readiness.md`](WORKLOG_20260907_cam03-identity-evidence-readiness.md) |
| 2026-09-07 | cam03 Recognition threshold experiment | EXPERIMENT COMPLETED / IDENTITY UNAVAILABLE | Read-only existing Provider comparison: 0.65 passes similarity but fails missing cam03 historical revisions; no manual assignment or publication. | [`WORKLOG_20260907_cam03-threshold-experiment.md`](WORKLOG_20260907_cam03-threshold-experiment.md) |
| 2026-09-06 | cam03 new-video behavior/member validation | BEHAVIOR REPLAY PASS / MEMBER IDENTITY BLOCKED | Tightened only the new video's second-guitar initialization bbox, completed the existing offline Stage5/7C/8/9 replay with six facts, and retained identity as unavailable because the cam03 capability profile is unresolved despite complete Coverage. | [`WORKLOG_20260906_cam03-video-member-validation.md`](WORKLOG_20260906_cam03-video-member-validation.md) |
| 2026-09-06 | DPI-01E.2 real Recognition wiring correction | CONFIGURATION LANDED / HISTORICAL CAM03 EVIDENCE UNAVAILABLE | Committed the formal cam03 mapping, verified normal CLI loading, audited the frozen interval with read-only Coverage/Inbox checks, and retained fail-closed identity results because Coverage ended with `heartbeat_end_missing` and the cam03 capability profile is unresolved. | [`WORKLOG_20260906_DPI-01E2_real-recognition-wiring.md`](WORKLOG_20260906_DPI-01E2_real-recognition-wiring.md) |
| 2026-09-06 | DPI-01E.2 cam03 Recognition evidence | BLOCKED / CAM03 CONFIGURATION UNAVAILABLE | Verified the frozen source/replay and 12 Stage9 events, derived the confirmed half-open time window, audited config without exposing secrets, and retained all 12 facts as anonymous. | [`WORKLOG_20260906_DPI-01E2_cam03-recognition-evidence.md`](WORKLOG_20260906_DPI-01E2_cam03-recognition-evidence.md) |
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
