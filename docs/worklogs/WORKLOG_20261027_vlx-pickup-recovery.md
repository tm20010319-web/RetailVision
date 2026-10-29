# VLX pickup recovery and third recording validation

Date: 2026-10-27
Status: IMPLEMENTED / REAL VIDEO TEST COMPLETE / ACTION ACCURACY NOT ACCEPTED

## Authorization and scope

User supplied `D:/surveillance_video_raw/2026_10_27_19_16_36.mp4` and explicitly
selected **fix missed pickups before testing**. Keep the same six physical
targets, video-local anonymous users, arbitrary pickup/putdown locations,
source-PTS timing, and backend-only artifact output. No DB, frontend, member
identity, legacy SAM3 or production-gate changes.

The diagnosing-bugs workflow was used. Previous real runs remain immutable:
`test_20261027_170342_v1` and `test_20261027_175203_v1` under the external runtime
root. Their original counts must not be relabeled as fixed-model results.

## Reproduction and findings

Replaying the complete second-camera `observations.jsonl` through the actual
InteractionAnalyzer reproduced zero events in 0.055 seconds. A 12-second window,
one object (robot dog) and one anonymous user, reproduced twice in <0.001s.
The committed minimized fixture retains source frame numbers and real boxes /
wrists with the source SHA-256. Regression failed with `0 == 1` before the fix.

At 117.081–117.481s the object had a resting baseline and visible hand contact.
At 117.681s a fast upward move produced `continuous=False`; the old rule deleted
the baseline and contact history. Subsequent held observations could no longer
establish a new pickup. A real source-PTS gap also occurs at 118.481–119.561s.

A separate person-detection probe on exact source frame 2340 (96.081s) returned
no people at YOLO11n-pose imgsz=960. With the same weights, CPU and confidence,
imgsz=1280 returned the visible operator and both wrists (confidences 0.925 and
0.739); 1600 also detected them. The configured input size is now 1280.

An initial recovery change exposed a false guitar recovery at 103.881s: one
wrist passed near a stationary small guitar, then another wrist held a different
guitar already carried into view. A second captured fixture went red on this
false event. Tracking the original left/right wrist, rather than switching to
whichever is nearest, removes this false recovery.

Reviewing the third recording found another false guitar event at 133.641s:
the object box changed before hand contact, while the guitar remained on its
stand. A third real-trace regression requires object displacement after contact,
preventing old box motion from being combined with later wrist motion.

## Final implemented rule change

- Retain observed precontact / pickup candidates only within the existing
  two-second missing-evidence grace.
- Require an observed resting origin, the same user and same wrist, and
  compatible hand/object motion to recover across a tracking break.
- Missing contact contributes zero confirmation time. A reacquired sample alone
  cannot confirm pickup; only supported subsequent observations count toward
  the unchanged 0.4-second confirmation requirement.
- Unknown original wrists can wait briefly, but a visible different wrist
  cannot replace the original contact evidence.
- Require object displacement after the observed hand contact; a precontact
  bounding-box change is not sufficient evidence of a pickup.
- Preserve discontinuity counts, source timestamps, conservative user changes,
  ambiguity, long-loss and EOF semantics. No synthetic putdown.
- Initially contacted/held objects without an observed pickup show unknown
  state instead of claiming a resting state.
- Manifest/report now record policy `observed_same_hand_postcontact_motion_v3`.

## Verification

`python -m pytest test/test_vlx_interaction_video.py test/test_vlx_live_tracking.py test/test_vlx_dual_support.py -q`
passes **57 tests** (30 interaction/file tests + 27 existing support tests).
Existing SQLAlchemy deprecation warning remains. This is not accuracy acceptance.

Full second-camera trace replay now yields one robot-dog closed session:
117.681 → 125.561s, 7.880s, confirmed at 120.161s. The spurious guitar recovery
is absent. This reuses prior model observations; it is not fresh model inference
and the boundary accuracy is not independently accepted.

First-video trace replay retains two closed dog/bee sessions and one open guitar
session (the prior suspected false guitar pickup remains a known limitation).
The previous late bee open session is no longer confirmed under same-wrist
evidence. No claim of unchanged real accuracy or all misses fixed.

Real VLX first-frame comparisons tried added distractor labels and more detailed
descriptions. Neither consistently improved fixed-instance localization; some
labels became worse. Neither query experiment was adopted. New-video first-frame
guitar/dog/bee localization is plausible, while egg/plush instance mistakes
remain. Raw comparisons, captured source sheets and full trace replays live at
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/diagnostics_20261027_191636/`.
These are diagnostic evidence, not implementation authority.

## Completed third-recording test

New video: HEVC, 2560x1440, 183.681 seconds, 4432 source frames.
Fresh inference: `D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_191636_v2`.
All 900 sampled observations through source frame 4432 were processed, using the
unchanged original six queries, policy v2 and pose size 1280. Inference took
2516.1934 seconds. Its original results and loaded-code snapshot are preserved.

Final output: `D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_191636_v3_replay`.
Policy v3 was replayed against those exact model observations without rerunning
inference. Source hash, frame numbers, timestamps, objects, people and diagnostics
were checked unchanged. The replay/render took 73.803 seconds; manifest/report
explicitly identify the reused model evidence and original inference run.

Final machine output: **4 pickups, 3 putdowns, 3 completed sessions, 1 incomplete**.
Completed holding total is **18.40 seconds**:

| Object / anonymous user | Pickup (source seconds) | Putdown | Holding | Status |
| --- | ---: | ---: | ---: | --- |
| Bee / user 2 | 72.360 | 74.960 | 2.600 | completed |
| Bee / user 2 | 119.041 | 128.241 | 9.200 | completed |
| Robot dog / user 2 | 128.641 | 135.241 | 6.600 | completed |
| Bee / user 6 | 142.641 | null | null | object_lost; attribution uncertain |

The incomplete bee session was last observed at 144.041s (1.4 seconds observed);
no putdown or completed duration was fabricated. IDs are video-local tracking
labels: the 10 IDs in the report do not establish 10 distinct real people.

The final H.264 annotated video (183.8s / 919 frames) and action excerpt
(68–180s, 112s / 560 frames) both passed full ffmpeg decoding. Video presentation
is 5 fps; event times still use source PTS. See final `REVIEW.md`,
`verification.json` and `preview_restored.json` for evidence and provenance.

### Remaining accuracy failures

- Visible guitar operations around 98–116s and 139–160s are missed. Object extent
  changes between the full instrument, body and neck break consistent tracking.
- The second bee release and dog release are detected late; their reported
  holding times are estimates, not accepted action boundaries.
- Later dog and bee operations remain missed or incomplete.
- Egg, pink-plush and orange-toy labels sometimes select unrelated objects.
- Anonymous user IDs fragment across occlusion/reentry. They do not reliably
  represent unique users. 2D co-motion also cannot fully distinguish lift/slide.

This fixes the reproduced short-break candidate loss and two false-recovery
patterns, but does not resolve all missed detections or qualify action accuracy.
Remaining engineering work: fixed-instance grounding, consistent object extent,
release timing and anonymous-ID continuity. No DB or frontend change was made.

## Preview restoration completed

The shared GPU preview was stopped gracefully from runtime
`20261027_184819_713856`, PID 27196. Its current selection had changed to **[0]
(小蜜蜂)**, so do not restore the earlier guitar selection. Saved restoration
record: `D:/project/RetailVisionDev/runtime/vlx-interaction-video/preview_restore_191636.json`.
After analysis the native unlimited-duration preview was restored with the same
config and `--target 小蜜蜂 --duration 0`. New runtime:
`D:/project/RetailVisionDev/runtime/toy-guitar-vlx-dual/20261027_203351_971868`,
PID 54456. The window exists and both feeds are fresh with advancing source-frame
sequences. The GUI selection subsequently changed to [1, 2] at revision 3; that
current selection was preserved rather than overwritten with the launch target.
