# Worklog: Staff Greeting Offline Replay

Date: 2026-08-30
Status: `IMPLEMENTED / REAL MP4 REPLAY FINALIZED`

## Frozen Runtime Boundary

- Official Staff Greeting production path is Offline MP4 replay.
- Realtime Staff Greeting remains legacy compatibility and is gated by
  `BEHAVIOR_REALTIME_GREETING_ENABLED=false` by default; V2 is a child shadow
  switch and cannot bypass the parent.
- Offline uses the shared `OfficialGreetingCore`, verified presentation PTS,
  track-only identity, artifact-only staging/finalization, and no member or
  behavior database publication.
- PTS/config failure is `NOT_ELIGIBLE` diagnostics-only. A pending cycle at a
  verified-media EOF is finalized as `cancelled` with reason `video_eof`.

## Implementation

- Added `agent/staff_greeting_offline.py` with OpenCV PTS validation, every-frame
  replay, shared Core orchestration, read-only overlay, JSONL artifact,
  pure same-run Summary aggregation, idempotent FINALIZED manifest handling,
  and fail-closed diagnostics.
- Added `scripts/run_staff_greeting_offline.py` as the explicit MP4 entrypoint.
- Added realtime parent/child gate coverage and Offline artifact/PTS tests.
- Kept Legacy Realtime APIs and identity-capable path intact.

## Validation

- Focused runtime and offline suite: `57/57 passed` after closeout timestamp
  and staff-null coverage was added.
- Supplied MP4 replay: 1433 decoded input frames and 1433 output frames at
  2560x1440, PTS timeline verified. The final rerun used the `agents-py311`
  environment with the NVIDIA GeForce RTX 5070 Ti (`torch 2.11.0+cu128`).
- Finalized closeout run:
  `outputs/staff_greeting_offline_closeout_20260830/a0c99f6fb27c4f8ba398ea514907ecd4/`
- Replay summary: `total_events=1`, `confirmed_count=0`,
  `cancelled_count=1`; event reason `customer_left_greeting_area`.
- The cancellation artifact maps Cycle start PTS to `started_at` and terminal
  PTS to `ended_at`; no staff track was bound, so `staff_track_token` remains
  null without fallback assignment.
- Closeout hardening added Summary envelope schema/status gates and an atomic
  `FINALIZED` manifest commit (`tmp` write, flush, fsync, replace). The focused
  regression suite now passes `62/62`; no Official V1 Greeting Logic changed.
- An initial `cuda:0` attempt from the Conda `base` environment correctly
  failed closed because that environment has CPU-only `torch 2.13.0+cpu`.
  The machine GPU and driver were available; the completed CUDA replay used
  `agents-py311`, where `torch.cuda.is_available()` is true.

## Remaining Boundary

The example camera calibration is `provisional`; the overlay exposes this as
`CALIBRATION: PROVISIONAL`. No staff/member identity inference or database
publication was added.

## Staff Greeting Demo Effect Validation P0 (2026-08-31)

- Built deterministic diagnostic replay:
  `scripts/diagnose_staff_role_integrity.py` captures only the 64-70 second
  diagnostic window while processing the full prefix, and can replay the
  captured person/vest facts without model inference. The baseline was RED on
  two consecutive replays at source frame 1649 / PTS `68.653208s`, where
  Track23 became `staff` for 27 frames.
- Root cause was upstream person detection/tracking integrity, not Greeting
  Core or RoleResolver hysteresis. At `68.487279s` the vest was assigned to
  Track42; at `68.528761s` the black-shirt customer and blue-vest employee
  were emitted as one merged person box, Track42 disappeared, and the same
  real vest became the only geometric candidate for Track23. It was assigned
  at `68.528761s`, `68.611726s`, and `68.653208s`, reaching the existing 3/5
  staff threshold. No duplicate vest detections occurred.
- Track20 last appeared at source frame 1242 / `52.350664s`; Track42 first
  appeared at frame 1286 / `54.134403s` (gap `1.783739s`). Track57 first
  appeared at frame 1598 / `66.620575s` for the flower-shirt customer while
  Track42 carried the blue-vest employee evidence. Manual truth is recorded
  in `test/fixtures/staff_greeting_role_truth_64_70.json`.
- Minimal fix: vest association now rejects the lower placement produced by a
  merged person bbox (`torso_bottom=0.60`) instead of attributing it to the
  customer; the RoleResolver remains fail-closed for ambiguous candidates.
  Offline production replay exposes `--person-imgsz` and defaults to the
  validated `1280` candidate. No tracker default, Greeting threshold, Core,
  event schema, PTS, calibration, identity, or realtime gate was changed.
- Added diagnostic association snapshots, focused RoleResolver regressions,
  and the diagnostic-only ByteTrack tuned candidate config. Role/vision
  focused tests pass (`28/28`); Official V1 Greeting tests pass (`15/15`).
  The full MP4 rerun with CUDA `cuda:0`, person `imgsz=1280`, and the supplied
  blue-vest YOLO model finalized successfully:
  `outputs/staff_greeting_offline_p0_fixed_20260831/200a12faca9148f28308f38383dc63f9/`.
  It decoded and wrote `1808/1808` frames at `2560x1440`, with verified PTS,
  `total_events=2`, `confirmed=0`, `uncertain=1`, `cancelled=1`; Track23 did
  not become staff and no `customer_role_corrected_to_staff` event was
  emitted.
- The generated video remains an eligible analyzed artifact even with zero
  confirmed greetings. The summary correctly keeps `confirmed_count=0` and
  does not reinterpret the remaining uncertainty/cancellation as success.
- After redirecting the managed shell's temporary directory to the workspace,
  the complete related vision/Core/Offline suite passes `46/46`; the actual
  CUDA replay also completed successfully.

## Staff Greeting Demo Effect Validation P0.1 (2026-08-31)

- Added the MP4-local manual subject fixture
  `test/fixtures/staff_greeting_subject_truth_64_70.json`. It uses
  `pixel_xyxy`, binds provenance to source SHA-256, contains no Track IDs, and
  annotates the black-shirt customer, blue-vest employee, and flower-shirt
  customer at six PTS keyframes: `66.993916`, `67.491704`, `67.989491`,
  `68.155420`, `68.694690`, and `69.026549` seconds.
- Extended `scripts/diagnose_staff_role_integrity.py` with diagnostic-only
  `--subject-truth` validation. It matches each manual bbox to candidate-run
  tracks using an IoU gate plus mutual center containment, fails unresolved on
  insufficient or ambiguous matches, and never falls back to nearest Track or
  baseline Track IDs. The original fixed-Track captured-facts assertion remains
  available only for deterministic replay.
- Consuming the existing CUDA `imgsz=1280` 64-70 second diagnostics produced
  `outputs/staff_greeting_role_integrity_p0_1/staff_subject_truth_report.json`.
  All 18 assertions pass: black-shirt customer -> candidate Track13,
  blue-vest employee -> Track24, flower-shirt customer -> Track19. Across all
  six keyframes both Customer subjects remain `customer` with `0/3` Vest hits
  and no assigned Vest; the employee remains `staff` with `5/3` window hits and
  the Vest assigned to its current Track.
- The captured baseline facts replay remains GREEN under the fixed upstream
  association logic: Track23 was observed for 239 frames and never became
  `staff`. New subject-matcher regressions cover candidate Track-ID changes,
  Customer false-Staff failure, correct Staff plus Vest evidence, and ambiguous
  match fail-closed behavior. The related vision, Official V1 Core, Offline,
  and realtime-gate suite passes `51/51`.
- Tracking impurity is not claimed fixed. Fragmentation, lost/reacquired tracks,
  and ID switches remain observable diagnostic facts. The closed P0 claim is
  narrower: the demonstrated merged-person-box failure no longer transfers
  Staff Vest evidence into either manually identified Customer subject.
- No ReID, face/member/staff identity, production track stitching, Greeting
  Core change, Greeting threshold change, area/EOF change, event/artifact
  contract change, calibration change, or realtime/offline boundary change was
  introduced. This closes Staff Role Integrity P0; investigation of why the
  valid pair terminates `uncertain` belongs to P1.

## Staff Greeting Demo Effect Validation P1 (2026-08-31)

- Added SHA-256 and frame-size provenance to diagnostic captures. Subject
  Truth validation now fails closed with `subject_truth_source_mismatch` or
  `subject_truth_frame_size_mismatch`; focused correct/wrong-source/wrong-size
  tests pass. The new CUDA diagnostic verifies both provenance and the existing
  18/18 subject assertions.
- A deterministic artifact loop reproduces the target Cycle as `uncertain`.
  The diagnostic rerun used the frozen `cuda:0`, person `imgsz=1280`, current
  ByteTrack, Staff Role fix, verified PTS, and existing camera configuration.
  It recorded 156 rows over PTS `64.007190-70.188053` and reproduced terminal
  PTS `70.063606` with confidence `0.518622935`.
- A diagnostic-only real-Core subclass records post-transition Pair state; it
  does not change a Core branch or formal artifact schema. G1-G5 and G7-G9
  pass at terminal. Current-window G6 is false, but approach was already
  latched at `67.782080`, so it is not terminal-blocking. G10 alone fails:
  `0.518622935 < confirmed_confidence 0.7`.
- The Core's pair confidence is the lifetime cumulative minimum of Customer
  person, Staff person, and Staff role confidence. G10 first fails at
  `68.155420` when Staff person confidence drops to `0.590268910`; the final
  minimum comes from Customer person confidence `0.518622935` at `68.362832`.
  Staff role confidence stays `1.0`. The drops occur during the strong overlap
  interval and are not a Vest-role failure.
- `within_since` first starts at `67.782080`, resets at `68.362832` only because
  distance is `1.351617m > 1.2m`, then restarts at `69.026549` and remains
  uninterrupted for `1.037057s` until terminal. Target pair tokens do not
  switch or recreate; Greeting Area also remains stable after Staff eligibility.
- Root cause classification is `CONFIDENCE_GATE_BLOCKER`. No Greeting threshold
  or Official V1 business semantic changed. Lowering the threshold is not
  justified from one MP4; any sustained-window/robust confidence aggregation
  proposal is a separate semantics design phase.
