# VLX full-recording test: 2026-10-28 01:39:57 upload

Date: 2026-10-28
Status: TEST COMPLETE / ACTION ACCURACY FAILED / PREVIEW RESTORED

User confirmed testing the entire uploaded recording with the current fixed
version and delivering a playable video with pickup/putdown counts and holding
timers. Toss/catch stays one session; placing down and picking up starts another.
No runtime, frontend, database or identity changes are part of this test.

Source: `D:/surveillance_video_raw/2026_10_28_01_39_57.mp4` (245.561 seconds,
5,960 source frames, 2560x1440 HEVC). The filename and embedded camera clock
differ; all reported action times use source-relative presentation timestamps.

Output: `D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261028_013957_v6`.
Companion `_meta` directory retains the source hash, implementation snapshots,
supervisor logs, visual QA and preview restoration state. Fresh VLX inference
uses `tracked_correction_rest_repeat_v6`, with runtime hashes checked against
the previous final deliverable. Original runs remain untouched.

Preflight succeeded. The supervisor saved the actual native preview selection
[1, 2] (large/small guitar), requested graceful pause, and restored the original
live environment in `finally`. Restoration was verified after inference.

Source review shows several users handling objects around 33–125s. The full
recording, including the later quiet interval, was analyzed unchanged.

## Final inference result and visual comparison

All 5,960 source frames were decoded/tracked, with 1,209 analysis samples;
last analyzed PTS is 245.521s. Fresh inference completed successfully in about
49.4 minutes. The current runtime hashes still match the prior delivered v6.
No rules, thresholds, model configuration, frontend, database or production
gates were changed during this current-version test.

System output: **4 pickups, 2 putdowns, 2 incomplete sessions**. This is not an
accuracy pass. Visual source review distinguishes at least nine clear operation
spans (three each for bee, dog and guitar). Only the first three have matching
pickup onsets. Six subsequent onsets are missed, and a fourth system pickup is
a false completed dog session after the object was already put down.

| Session | System interval | System result | Visual review |
| --- | --- | --- | --- |
| bee_plush:1 / user2 | Pickup 33.960; last seen 58.640 | Incomplete, object_lost | Still held before actual return around 66s; two later pickups missed. |
| yellow_robot_dog:1 / user4 | 41.440–53.240 | Completed, 11.800s | Actual return around 48–49s, so reported duration is too long. |
| white_guitar:1 / user4 | Pickup 51.440; last seen 97.721 | Incomplete, possible_handover | First return around 57s was not closed; timer crosses another user's later handling; later pickups missed. |
| yellow_robot_dog:2 / user2 | 108.561–109.361 | Completed, 0.800s | False pickup: source shows supported object and user departing. |

The raw completed total of 12.6s includes the false 0.8s and delayed 11.8s records;
it must not be presented as accurate holding time. No fabricated putdowns were
added to incomplete sessions. Source sampling does not precisely adjudicate
possible brief extra support contacts around 44.5–47s, so nine is a conservative
set of clear spans, not a millisecond-level exhaustive truth annotation.

Other real failures: pink-plush/white-egg instance confusion; white guitar
localization jumping to the cyan small guitar at 121.96s; anonymous-ID drift,
extra person boxes, and user 4 changing from the light jacket to black jacket.
The remaining source interval after 130s produced no extra action events.

## Deliverables and verification

In the output directory:

- `annotated.mp4`: 245.6s, 1,228 frames, H.264, 2560x1642, 5fps.
- `actions_28s_to_130s.mp4`: 102s, 510 frames, same format; overlay retains source time.
- `result.json`, `events.jsonl`, `observations.jsonl`, `manifest.json`: original fresh inference evidence, unchanged.
- `REVIEW.md`, `visual_source_review.json`, source/encoded QA images: actual failure comparison.
- `verification.json`, `implementation_hashes.json`, `preview_restored.json`: media, provenance and restoration checks.

Both videos fully decode and encoded action/timer/end frames were inspected.
Read-only decision reconstruction matches every original decision. The previous
147-test baseline was not rerun for this unchanged runtime and is not evidence
that the new recording passed recognition acceptance.

Preview restored and verified at 2026-10-27T18:36:05Z: PID 53136, runtime
`toy-guitar-vlx-dual/20261028_023511_499099`, selection [1,2], native visible
window, both feeds fresh and frame sequences advancing. No preview environment
or target selection changes remain.
