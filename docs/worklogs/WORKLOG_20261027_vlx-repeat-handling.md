# VLX repeated handling with toss/catch continuity

Date: 2026-10-27
Status: IMPLEMENTED / FINAL RECORDED-MODEL REPLAY PASS / PREVIEW RESTORED

User confirmed that tossing and catching the item counts as one handling
session; only placing it down and subsequently picking it up starts another.
Continue the six-instance backend with VLX localization, anonymous users,
source-PTS timing and arbitrary support locations. No frontend, DB or SAM3
inference change. Old production Stage9 remains unchanged.

Baseline: the real 21:12:59 observation trace reproduces zero events in 0.012s.
Previous read-only comparison ran 100 tests successfully and fed two sequential
same-user/same-object operations through old Stage9: two distinct sessions with
two pickups and two putdowns. This was controlled evidence, not new SAM3 inference.

Ranked causes to probe:

1. Old incorrect foreground bee boxes block repeated later correct VLX boxes
   through the global jump rejection. Requiring repeated consistent model
   correction should permit a new track segment without inventing movement.
2. Sparse VLX confirmations plus brittle flow lose fast rotating/tossed objects.
   Source-pixel tracking between recorded confirmations should increase real
   observation coverage without extending stale boxes as fabricated detections.
3. Release requires both wrists away; supported rest with a nearby hand never
   closes. Sustained return to an observed resting pose should close a session,
   while airborne motion and short toss gaps preserve it.

New diagnostics and before-change implementation will live under
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/diagnostics_repeat_handling_20261027/`.
Original v4 outputs remain immutable. The dual preview is running and must stay
unchanged during CPU-only recorded-model/pixel experiments. Any later fresh
inference pause must save and restore the current preview selection.

## 2026-10-28 continuation

Implemented `observed_rest_return_repeat_v5` in the independent VLX backend.
The original minimal regressions failed at the expected two seams (2 failed,
1 passed): stale wrong track suppressed repeated correct model boxes, and
nearby-hand rest prevented a new session. Both now pass.

- Repeated consistent model correction retires the old track without creating
  displacement. A separate candidate optical-flow track verifies any stationary
  interval used to rearm a previously observed resting origin.
- The original color-only probe drifted onto skin and was rejected. CSRT follows
  the rotating bee much better; preserve corroborated appearance during motion
  and reanchor when sequential model boxes stabilize. Pixel tracking must stay
  valid, and the original VLX-confirmation age is bounded at 6s. Actual missing
  object evidence retains the existing 2s grace.
- Supported return to an observed resting pose can close with a hand nearby.
  Its first reacquired sample can start, but cannot finish, confirmation. No
  timestamp is invented inside the 38.72–39.76 source gap.
- CSRT is isolated to the file-analysis adapter. Existing live callers still
  use their original FlowTrack factory. A new `vlx-interaction-py311` venv borrows
  VLX dependencies and installs opencv-contrib-python 5.0.0.93 locally; the
  original preview environment was not changed.
- Exact-PTS raw-model replay has explicit provenance; original v4 evidence is
  unchanged. `csrt_reanchored_v5` completed with 2 pickups, 2 putdowns, 0 incomplete
  sessions: 24.12–39.76 (15.64s), 42.16–49.76 (7.60s), total 23.24s. This is
  recorded-model/new-pixel-tracking evidence, not fresh model inference or exact
  physical-boundary accuracy. Both releases are later than visual source review.
- 134 regression tests passed before adding the real two-session trace fixture.
  Fresh full-video inference is running at `test_20261027_211259_v5`; its metadata
  includes implementation hashes and loaded source snapshots. Supervisor saves
  actual preview selection [1,2] and restores the original preview environment
  in `finally`. Final result and restoration verification remain pending.

## Final result (supersedes the intermediate states above)

Final policy: `tracked_correction_rest_repeat_v6`.

Further fresh inference exposed single contradictory labels suppressing valid
tracks, shifted orange-toy labels claiming the already held bee, and partial
resting extents observed while a hand was nearby. Final changes preserve a
pixel-verified existing track while a solitary jump is pending, prohibit new
labels from claiming its overlapping center, reject contradictory yellow/pink
attributes for the user-confirmed pink instance, and retain anchored partial
resting references. Raw multi-label ambiguity remains unknown when there is no
established physical track. No color-only tracking was shipped.

The complete fresh v5 test remains at `test_20261027_211259_v5` (one completed
session; first missed). A second fresh VLX run on a losslessly copied first
60.04s is at `test_20261027_211259_v6_fresh60` (two pickups, one completed,
one interrupted in that loaded revision). The derivative has 1,374 frames,
pixel stream copy and exactly matching source PTS. Both fresh runs are kept
unchanged; neither is relabeled as final success.

**The final deliverable is only**
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_211259_v6_verified`.
It reuses the second fresh run's raw VLX and pose evidence on the exact source
frames, recomputes tracking and final rules, and explicitly records
`recorded_model_source_pixel_replay` / `fresh_model_inference: false`.
Earlier directories containing `final`, `validated`, or `v6` in their names
are intermediate diagnostics, not the delivered result.

Final result: **2 pickups, 2 putdowns, 0 incomplete, only bee_plush/user1**.

| Session | Pickup | Observed putdown | Holding |
| --- | --- | --- | --- |
| bee_plush:1 | 24.12s | 39.76s | 15.64s |
| bee_plush:2 | 42.16s | 49.56s | 7.40s |

Total holding: 23.04s. Toss/catch does not create additional sessions. The
first putdown is later than visual support contact (38.4–38.72s), including the
source gap to 39.76s; the second is also delayed relative to visible contact
(48.8–49.2s). These are observed visual estimates, not precise physical timing.

Verification: 147 tests passed across interaction, pixel tracking, live/dual
support and the old product/association/offline suites. Real regression fixtures
cover both repeated bee operations and all six targets to catch duplicate-label
counts. Existing guitar and dog safeguards still pass. No debug prints remain
in repository runtime code; probes are confined to the diagnostic directory.

The 60.2s H.264 preview (301 frames) and 36s action clip (180 frames) fully
decode. Visual review confirms the holding timer beside the bee (3.8s near
source 27.92s), reset for the second session, and final 2/2 counts. Checks,
hashes, source derivation, screenshots and provenance are in the delivered
directory. Pink-plush localization still sometimes selects a pink guitar;
this clip produces no extra session for it. Other objects/cameras remain
unqualified. No frontend, database, identity or production-gate work was done.

Preview restoration verified at 2026-10-27T17:14:32Z: PID 56764, runtime
`toy-guitar-vlx-dual/20261028_010639_232198`, selected targets [1,2], visible
native window and both camera frame sequences advancing. Original preview
environment is unchanged. Final `preview_restored.json` records the evidence.
