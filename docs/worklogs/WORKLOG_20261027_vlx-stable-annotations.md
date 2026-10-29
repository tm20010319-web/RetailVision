# VLX annotation continuity across missed and ambiguous confirmations

Date: 2026-10-27
Status: LIVE / 40-SECOND CURRENT-SCENE CONTINUITY CHECK PASSED / SEMANTIC IDENTITY OPEN

## Request and reproduced fault

User reports boxes disappearing/reappearing while objects and query remain
unchanged, then requests a solution and supplies the header message saying
the large-guitar candidate is non-unique. Scope is the existing independent
two-camera preview; original YOLO and production/Offline V1/identity paths
are unaffected. No new model training or physical-instance qualification.

Read-only observer/replay command:

`D:/project/RetailVisionDev/envs/vlx-seek-py311/Scripts/python.exe -B D:/project/RetailVisionDev/runtime/toy-guitar-vlx-dual/diagnostics/static_flicker_20261027/check_flicker.py`

An initial 22-second trace recorded two losses of BOTH winners with unchanged
query revision and fresh cameras. Exact losses were at UTC 03:01:11.184277
and 03:01:19.415938. Camera-3 frame ages were 0.031/0.015s. Each followed
an empty usable detection list: multiple electric-guitar candidates caused
class-1 suppression, while the ukulele query returned no region. The tracker
unconditionally erased old tracks before applying each confirmation. Model
outputs also swapped the labels between positions in other rounds.

The saved trace replays to the same failing assertion. Evidence is outside
the repository under `toy-guitar-vlx-dual/diagnostics/static_flicker_20261027/`.
`before_fix_trace.json` and `before_fix_disappearances.json` preserve it.
New regression tests first failed on empty-result clearing, missing candidate
reconciliation and label swaps; the current focused suite passes 54 tests.

## Changes

- Recognition now retains parsed candidate detections in addition to its
  ordinary uniqueness-filtered output. No threshold lowering, fixed camera
  coordinates, region-size identity assignment or proposal changes.
- Delayed candidates are replayed through the same grayscale history into
  the CURRENT frame before matching existing tracks. A class with multiple
  candidates may renew only when exactly one propagated candidate overlaps
  the strong existing same-class track at IoU >= 0.5. Ambiguous candidates
  cannot initialize a track without prior evidence.
- Empty or unmatched confirmations may retain only live tracks with appearance
  correlation >= 0.65, >= 50% feature support and remaining confirmation TTL.
  Per-frame flow, appearance rejection, camera-gap clearing, source freshness,
  bounds and selection-revision clearing remain active.
- New labels essentially covering another live class (IoU >= 0.7) are
  rejected rather than instantly swapping the two names. Prior identity can
  still have been wrong: continuity is not physical-instance recognition.
- Held tracks keep their ORIGINAL model frame, confirmation UTC and monotonic
  age. Repeated misses cannot renew these. API frame metadata now comes from
  the individual track, preventing a missed query from appearing to re-confirm
  all targets. API schema remains compatible, patch version 1.0.1.
- Main dual-view caller supplies candidates and records reconciliation
  diagnostics. Resolved ambiguity no longer says the target was discarded;
  an old held box is visibly labeled 跟踪中. Single-view code benefits from
  bounded missed-confirmation retention via the shared tracker, but does not
  yet use the dual caller's raw-candidate resolution.

## TTL measurement and decision

Keeping the original eight-second TTL fixed the large-guitar ambiguity path,
but a 40-second intermediate test still recorded THREE small-guitar losses:
repeated misses and ~4.1s per-camera cycles let confirmation age exceed eight
seconds before a new positive completed. Seven ambiguity-match and seven
held-miss observations demonstrate that the new paths were exercised; this
intermediate failure is retained in `ttl8_after_candidate_match_trace.json`
and `ttl8_after_candidate_match_disappearances.json`.

The dual profile now uses a **16-second** maximum model-source age, enough
for the observed short repeated misses plus inference lag. This is bounded
visual tracking, not an indefinitely frozen rectangle. Default standalone
tracker/quality helpers and the single-view configuration remain eight
seconds. Dual quality ranking receives the configured expiry so it cannot
silently discard valid dual tracks at the old eight-second threshold. Both
Markdown handoffs and OpenAPI were updated with the new bound and tracking
semantics. No claim that semantic ambiguity was solved by a longer TTL.

The first 16-second-budget live check still lost the small target once in
40 seconds after prolonged missing classifications, while keeping the large
target. Evidence is retained in `ttl16_before_isolated_recheck_trace.json`
and its disappearance record. Therefore the repair did not stop at increasing
TTL. The worker now rechecks at most ONE missing, previously uniquely detected
class per camera/revision, querying it alone on the SAME image and automatic
proposals. No isolated retry is run for a never-seen class or a class already
returned by the primary query. Raw retry output and class IDs are retained;
inference time includes retry cost. This tests possible combined-query
interference, not a promise that occlusion or identity is resolved. Camera 1
without any prior guitar does not pay a retry merely because camera 3 saw it.

## Verification and scope limits

54 focused checks pass, including actual HTTP/WS adapter regressions plus new
tests for repeated misses without timestamp renewal, delayed moving-candidate
replay, ambiguity without prior identity, label swaps, removal/clear, final
TTL expiration, configurable view quality and held-track API timestamps.
Two further checks cover one isolated retry, same image/proposals, no unseeded
retry, and at most one extra call per recognition frame; these failed before
the retry parameter/implementation was introduced.
Existing SQLAlchemy and Starlette/httpx deprecation warnings remain.

The current native preview was restarted preserving the selected guitars.
Final 40-second observation after isolated rechecks recorded ZERO winner
losses at the existing display snapshot resolution (~0.5s). Eight changes
of reconciliation state were captured: four matched-ambiguity observations
and five held-miss observations, so the corrected paths were exercised.
Saved-trace replay now passes as well. This is a current static-scene
continuity check, not every-frame qualification or semantic accuracy. The
additional model calls can increase confirmation interval; measurements are
retained in `final_metrics.json`. Raw model quality and arbitrary
placement/occlusion/identity remain unqualified. A read-only high
detail camera snapshot was captured for diagnosis, without changing device
settings or using manual boxes as inference proposals. The last source shows
several similar instruments near one another, so correct original physical
identity cannot be inferred from stable boxes alone.

No frontend API process was launched or credentials rotated during this
native-preview correction. Existing public-deployment records are history,
not a claim that the API is currently running.
