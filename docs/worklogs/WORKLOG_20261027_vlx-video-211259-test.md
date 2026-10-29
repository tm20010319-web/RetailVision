# VLX current-v4 test of the 21:12:59 recording

Date: 2026-10-27
Status: REAL MODEL TEST COMPLETE / TWO BEE OPERATIONS MISSED / PREVIEW RESTORED

The user supplied `D:/surveillance_video_raw/2026_10_27_21_12_59.mp4` and explicitly
chose to test the current repaired version before further fixes. No recognition
code or parameters are changed in this task. Current policy is
`observed_extent_rest_pose_recovery_v4`; implementation hashes match the existing
65-test v4 baseline. A loaded-code snapshot is saved with this test's metadata.

Source: HEVC, 2560x1440, 3000 frames, 125.720989 seconds. Source PTS remain the
time basis; native camera-clock text is not substituted for the source timeline.
All six fixed queries, anonymous users, sample/model cadence and pose 1280 remain
unchanged. No DB, frontend, SAM3 or production changes.

Output: `D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_211259_v4`.
Metadata/supervisor/source review:
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_211259_v4_meta`.

This is fresh model inference on a new recording, not an observation replay.
The original 19:16:36 v2/v3/v4 artifacts remain immutable.

## Source visual review before model result

Source-PTS contact sheets show one black-shirt operator making two bee-plush
pickup/putdown operations, with tossing/catching during the handling intervals.
Approximate reviewed boundaries (not frame-exact accepted ground truth):

- First lift: 23.6–24.4s; returns to the display around 38.4–38.72s.
- Second lift: 41.6–42.4s; returns around 48.8–49.2s.
- The hand remains close to/on the plush during the intervening resting period;
  check whether the current visible-wrist release rule merges the two actions.
- The hand finally moves away around 50.8–51.2s. The operator then leaves the
  display and the remaining recording is mostly an empty scene.

The source has a PTS gap between 38.72s and 39.76s; no new frames or exact action
boundary should be invented inside that interval. Model outcome is pending.

## Preview restoration obligation

Prior native preview: runtime `20261027_203351_971868`, PID 54456, selection [1, 2]
(大吉他、小吉他), revision 3. It was stopped gracefully via its `stop.request` to
free GPU memory. The supervisor records state before stopping and uses `finally`
to launch the same native unlimited preview with the saved target text after
the offline run exits. Verify the resulting window, both fresh feeds, and
selection after completion; preserve any later GUI changes.

## Completed result

Fresh model inference finished with exit code 0 in 1747.34423 seconds (29.12
minutes). Processed 613 samples through source frame 3000 / PTS 125.680989s.
Final **0 pickups, 0 putdowns, no sessions**. This is a detection failure against
the two visually observed display pickup/return operations, not evidence that
no actions occurred. No completed holding duration can be reported.

Original trace audit:

| Source interval | Samples | Bee box present | People present |
| --- | ---: | ---: | ---: |
| First handling, 23.6–38.72s | 73 | 18 | 73 |
| Intermediate resting contact, 39.76–41.6s | 10 | 10 | 10 |
| Second handling, 42–49.2s | 36 | 19 | 36 |
| After departure, 60s–EOF | 329 | 329 | 0 |

Presence counts are not accuracy. One anonymous user was tracked, from 15.08 to
58.4s, 208 observations. Missing/ambiguous bee boxes during fast movement and
`initial_contact_without_observed_pickup` after reacquisition prevent timers.
The intermediate resting contact also remains unknown. No false action events
occurred in the empty remainder. Pink-plush labels still select the pink ukulele
in reviewed frames. Native input face/border overlays are already in the source;
this run does not perform member identity association.

Source hash and implementation snapshot match; code/config were unchanged.
The existing 65-test baseline was verified by hashes, not rerun as an accuracy
test. Full annotated H.264 video is 125.8s / 629 frames; source 20–56s action clip
is 36s / 180 frames. Both passed full ffmpeg decoding. Selected exported frames
were visually reviewed, including the bee missing while visibly in hand at
27.92s. `REVIEW.md`, `verification.json`, `visual_audit.json` and
`preview_restored.json` are saved in the final output directory.

The supervisor restored native preview to runtime `20261027_214600_798799`,
actual PID 46268, saved selection [1, 2], revision 1. Window existence, both fresh
feeds, increasing source-frame sequences and current target selection verified.
No frontend, DB, SAM3 or production change. Further missed-action fixes are not
part of this user-selected current-version test.
