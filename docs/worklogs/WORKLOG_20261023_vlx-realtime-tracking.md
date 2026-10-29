# Realtime video and tracking between VLX confirmations

Date: 2026-10-23
Status: REALTIME PREVIEW RUNNING / 13 TESTS PASS / MOTION ACCURACY OPEN

User explicitly accepted realtime video plus realtime tracking boxes, with
VLX periodically confirming the two products. This supersedes the sampled-frame
display in `WORKLOG_20261023_vlx-continuous-preview.md`, but retains its VLX
model, official proposals and conditional horizontal-guitar query. The prior
YOLO paths remain intact. No SAM3/Offline V1/identity/publication behavior changes.

## Changes

`scripts/vlx_live_tracking.py` uses CPU Lucas-Kanade forward/backward optical
flow, RANSAC similarity transforms and appearance checks to update boxes.
History retains at most 240 grayscale frames / eight seconds. A delayed VLX
result is seeded on its exact source frame and propagated through newer
buffered frames before it can appear on the current image. Old pixel boxes
are never simply overlaid on new frames. Failed tracks, stale confirmation,
camera gaps and empty recognition results clear boxes; VLX can reacquire them.
This is bounded visual tracking, not a guarantee against all drift/occlusion.

`scripts/run_toy_guitar_vlx_live.py` renders fresh camera frames independently
of VLX, including during model loading. `latest.jpg` and `preview_status.json`
describe the live display; `recognition.jpg`, `source.png`, `status.json` and
`results.jsonl` describe slower VLX recognition. The UI distinguishes live
video/tracking from VLX's latest inference latency. Producer/consumer queue
replacement handles a concurrent reader safely.

## Verification

13 focused tests pass in both agents-py311 and the actual VLX environment
(OpenCV 5.0), including existing initialization contracts. Existing SQLAlchemy
deprecation warning only.

New tests exercise a moving textured object and delayed recognition: the
propagated box reaches the current position at IoU >0.90, while its IoU with
the old position is <0.25. Additional cases cover disappearance/reacquisition,
out-of-order and empty results, camera gaps, expired seeds and a rotating
textured object. Appearance comparison uses a rotation-aligned crop with
light smoothing, avoiding aliasing and axis-aligned crop mismatch. These synthetic
checks verify alignment/lifecycle, not real-camera motion accuracy.

The preceding sampled-display process was stopped gracefully before starting
the updated independent visible preview. The active run is found through
`D:/project/RetailVisionDev/runtime/toy-guitar-vlx-live/current.json`.

Initial live version `20261023_100343_826048` was visually inspected: the
current camera timestamp updates continuously, and both target tracks were
seen in the updated locations (bee at the exhibit table and guitar on the
green chair). A bounded 12-observation display check recorded 17.26--19.58
FPS, mean 18.82, and 92 additional displayed frames over approximately 5.5
seconds. Maximum time from frame reception to loop sampling was 0.047 seconds;
this is not end-to-end camera latency. VLX cycles ranged 1.78--3.20 seconds.
Only one of those 12 snapshots contained both tracks. Subsequent visual
inspection showed a person moving the guitar; tracking is not qualified as
motion-accurate by these display measurements.

That version was gracefully restarted with the rotation-aligned appearance
comparison after all 13 tests passed. The final live process is left visible
for user testing. Fast motion, occlusion, weak texture and drift rejection can
still cause temporary missing boxes until VLX reacquires a target. No universal
placement/pose/occlusion acceptance is claimed.

Final run `20261023_100618_853694` is running. Its bounded eight-observation
check recorded 16.00--17.42 FPS and 66 additional display frames over about
3.5 seconds, while VLX completed 11 recognition frames by the end of the
check. The last observation contained both optical-flow tracks; the latest
VLX cycle was 3.234 seconds. See `final_live_display_validation.json` in that
run. This confirms independent continuous display and periodic recognition,
not audited motion/instance accuracy.
