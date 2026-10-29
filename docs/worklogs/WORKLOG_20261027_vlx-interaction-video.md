# VLX uploaded-video pickup / putdown backend

Date: 2026-10-27
Status: IMPLEMENTED / TWO CAMERA VIDEO TESTS COMPLETE / ACTION ACCURACY NOT ACCEPTED

## Authorized scope

User requested the old SAM3-style pickup counts and hold intervals using VLX,
first as a backend, with frontend and persistence deferred. Input is uploaded
recording content/local video. Anonymous users are numbered by first appearance
within each video and actions are attributed to the corresponding user.
Putdown may occur anywhere, not only on the original table.

The six fixed targets were reviewed with the user: orange cartoon toy, white
egg-shaped toy, **pink** plush toy (user corrected its color), yellow robot dog,
white electric guitar and bee plush toy. The supplied 152-second annotated
SAM3 recording established expected presentation; user said to test together
after implementation. No inference acceptance run on it was performed here.

## Implementation

- Independent CLI `scripts/run_vlx_interaction_video.py` with YAML configuration,
  environment preflight, source hash, per-frame PTS, explicit failure reporting,
  fresh output directories, JSON/JSONL reports and a Chinese annotated MP4.
- Reuses existing VLX NF4 loader, cached automatic WeDetect proposals and
  bounded optical-flow tracking. Exact fixed labels, conflicting classes and
  multiple-instance candidates are handled before action assessment.
  Low-texture/failed tracks get an earlier fresh model confirmation.
- Local YOLO11n-pose provides people and wrists only, on CPU by default.
  Anonymous IDs use position/motion plus head-excluding clothing histograms;
  reentry candidates are matched conservatively and ambiguity remains unknown.
- Pure temporal evaluator requires an observed resting baseline, unique hand
  association and co-motion for pickup; sustained stability with visible wrists
  away for putdown. Detection/confirmation times are separately retained.
- Counts and complete-session holding intervals are grouped by object, user,
  and user/object. Missing/ambiguous evidence, potential handover, track loss,
  initially held items and EOF cannot fabricate complete interaction sessions.
- No database writes, API changes, changes to legacy SAM3 rules or live-process
  restarts. Documentation and the module inventory identify the independent tool.

## Verification

Command:

```powershell
& D:/project/RetailVisionDev/envs/vlx-seek-py311/Scripts/python.exe -m pytest test/test_vlx_interaction_video.py test/test_vlx_live_tracking.py test/test_vlx_dual_support.py -q
```

52 tests passed (25 new tests, 27 existing tracking/support tests). Includes
generated MP4 decoding, controlled injected vision, preview encoding and JSON
exports; action times/counts, repeated sessions/users, same-user multiple
objects, held-still, unknown wrists, short/long occlusion, handover, source-time
gaps, video-local reentry, parser ambiguity, live/direct-label conflict guards,
low-texture model rechecks, VFR PTS, failure reporting and non-overwrite behavior.
The existing SQLAlchemy declarative-base deprecation warning remains.

CLI `--check` locates all required runtime assets and imports; all new Python
modules compile. Existing local pose weights were loaded and a blank-image CPU
prediction completed. Six-target VLX inference and end-to-end real-action
accuracy are **not** established by these checks. No full repository test claim.

## Remaining acceptance

Use original six-item footage with deliberate pickups, holding still, releases
at a different place, repeat pickups and two users. Inspect raw object boxes,
anonymous ID continuity, wrist evidence, each boundary time, counts and incomplete
sessions. Clothing-based reentry and 2D grasp/release estimation can be wrong,
especially under occlusion or similar appearance. Actual performance and
thresholds remain to be evaluated on that footage. User guide:
[`docs/vlx_interaction_video.md`](../vlx_interaction_video.md).

## First user recording (later on October 27)

User supplied `D:/surveillance_video_raw/2026_10_27_17_03_42.mp4` after cancelling
the proposed old SAM3-video run. Original duration 185.046s / 4497 frames;
905 media-time samples processed using the unchanged v1 configuration.
Actual inference completed in 961.92s. Output:
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_170342_v1/`.
The complete 185.2s / 926-frame H.264 preview passed full decode verification;
`actions_from_02m30s.mp4` isolates the operation segment for viewing.

First-frame grounding found all six items. Raw machine results: robot dog one
closed session 163.285–172.526s (9.241s); bee one closed session
173.526–180.126s (6.600s), then one open pickup at 182.326s; guitar one open
pickup at 173.926s. These are **machine outputs, not validated truth**.
Visual checking found a missed orange-toy pickup and a suspected false guitar
pickup while it remained on the table. User labels are 1 for an earlier passer,
2 for the later black-clothed actor, 3 for the blue-clothed actor; reentry
continuity remains unqualified. Six-object detection counts range 853–904 of
905 samples, which is observation coverage rather than recognition accuracy.

No model, threshold, code or database changes were made during this run.
Raw results and failures are preserved; `REVIEW.md` alongside the output records
the caveats. Next work should address actual trace-backed action errors before
claiming acceptance. The cancelled old-video output directory contains partial
startup artifacts and must not be described as a completed analysis.

## Second-camera recording (later on October 27)

User supplied `D:/surveillance_video_raw/2026_10_27_17_52_03.mp4` to test the same
items from another camera. Original 2560x1440 / 242.521s / 5925 frames; unchanged
configuration processed 1195 real PTS samples through frame 5925 in 3022.77s.
Output: `D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_175203_v1/`.
The 242.6s / 1213-frame full preview and 55s / 275-frame action preview both
passed full ffmpeg decode. Trace totals and observation counts match result.json.

Raw machine result is zero pickups, zero putdowns and no sessions. This is a
**failed action-recognition result**, not evidence of no human activity: the
source clearly shows carrying, placing and subsequent lifting. At 96.081s a
visible operator is absent from person evidence; at 100.081s a held bee is
localized but remains resting; at 119.961s a held robot dog is localized but
still has no pickup. The white-guitar label initially lands on a different
small guitar, then later localizes the intended large guitar. Fixed physical
instance identity and cross-camera action reliability remain unqualified.

All six labels appear at least once, with observation counts orange 9, egg 745,
pink 779, dog 1088, guitar 1152 and bee 587 out of 1195. These counts include
incorrect localizations and are not accuracy scores. The main visible operator
is labelled user2; anonymous ID continuity is not independently accepted.
Sequentially decoded frame-matched review images, REVIEW.md and verification.json
retain the findings; no labels or events were manually corrected.

User explicitly approved pausing the GPU-sharing dual-camera preview and restoring
it after this run. Original runtime `20261027_173632_679729` stopped gracefully;
the native preview was restored at runtime `20261027_184819_713856`, PID 27196,
with original selection [1,2] (large/small guitar). Both camera feeds were fresh,
the window existed, and inference was running when verified. A sanitized
`preview_restored.json` records the check. No algorithm/configuration changes,
database writes or modifications to the legacy SAM3 chain were made.
