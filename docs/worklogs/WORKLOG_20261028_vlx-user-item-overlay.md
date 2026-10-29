# VLX per-user/item overlay and 01:51:25 recording

Date: 2026-10-28
Status: DISPLAY VERIFIED / FULL TEST COMPLETE / ACCURACY FAILED / PREVIEW RESTORED

User explicitly chose the current recognition version and requested a display
of which anonymous user picked up which item how many times. They confirmed
the format: user 2 / bee / cumulative 2 pickups / current 5.3s. This is a display
update and a new-video test, not authorization to silently change detection,
person association, or interaction policy during the comparison.

## Display change

`scripts/run_vlx_interaction_video.py` now passes the current analyzer report
to the overlay. A fixed-width sidebar lists every recorded user/item pair,
cumulative pickups/putdowns, the current holding timer or latest completed /
incomplete status. Counts remain after putdown. Only the report at the current
source timestamp is used, never final totals from the future. Long lists rotate
pages every 8s while encoded dimensions stay fixed. Active box labels include
user, item, cumulative pickups and current duration; low-resolution footage
uses smaller labels and avoids collisions between active action labels.

Recognition, pose, user association, tracking, interaction rules, target table
and configuration hashes remain identical to the prior delivered v6. Only the
file runner's presentation code changed; no frontend, database or live-preview
configuration change. Policy remains `tracked_correction_rest_repeat_v6`.

Validation: 56 existing interaction/video tests passed. Real recorded-observation
visual checks cover concurrent users, separate users handling the same item,
two pickups by one user, retained post-putdown counts and incomplete records.
Every reconstructed decision matched its original trace. No inference evidence
was rewritten, and these visual checks are not fresh detection accuracy tests.

## New recording

Source: `D:/surveillance_video_raw/2026_10_28_01_51_25.mp4`.
Source properties: 800x448 HEVC, 4,367 frames, 184.795 seconds. This is a different
camera view with the orange toy, white egg and pink plush. The six-target
configuration remains unchanged as requested. Source overview places the main
visible operations around 110–148s; the entire recording was analyzed.

Output: `D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261028_015125_user_counts`.
Companion `_meta` retains original runner snapshot, loaded implementation hashes,
visual layout checks, source images, logs and saved preview selection.

The supervisor saved the actual current preview selection, paused gracefully,
ran fresh full-video inference and restored the original live environment in
`finally`. Complete source, media and restoration checks passed.

## Final result

Full fresh inference: 4,367 frames decoded/tracked, 884 analysis samples,
last source PTS 184.754989s; processing 845.51s (about 14.1 minutes).

System output: **2 pickups, 0 putdowns, 2 incomplete sessions**.

| User/item | Pickup | Last observed | Result |
| --- | --- | --- | --- |
| user 1 / orange_cartoon | 112.448s | 118.248s | One pickup, object_lost; no completed duration. |
| user 2 / white_egg | 118.448s | 122.396s | One pickup, object_lost; no completed duration. |

Source review identifies at least six clear operations: each of the two users
handles each of the orange toy, white egg and pink plush once. Four pickup
onsets are missed, and both detected records lose tracking before release.
No completed duration is available; observed lengths 5.8s/3.948s are not full
holding times. Source visual ranges are approximate, not exact timing truth.

The lighting changes near 106s. Pink localization is lost afterward; a captured
139.916s result is rejected by the existing yellow-dominance attribute check.
This is one observed rejection path, not a complete root-cause claim. Orange
is briefly labeled as bee, and white-egg boxes sometimes select pink plush.
Current recognition limitations remain, as the user explicitly chose a current
version test. This turn did not alter recognition or event rules to improve counts.

## Delivered artifacts and restoration

`annotated.mp4`: 184.8s, 924 frames, 1280x744, H.264, 5fps.
`actions_102s_to_152s.mp4`: 50s, 250 frames, same format. Both fully decode;
encoded frames confirm per-user/item counts, timers and retained incomplete
records. `REVIEW.md`, source/encoded screenshots, `verification.json` and the
original raw inference files document the display pass and accuracy failure.

Final 56-test run is preserved in `regression.log`. Recorded-trace display checks
include two pickups for one user, two users handling one item, completed and
incomplete records; they are explicitly separate from fresh model evidence.
Only the runner display code differs from the previous v6 implementation hashes.

Preview restoration verified at 2026-10-27T19:04:37Z: PID 30140, runtime
`toy-guitar-vlx-dual/20261028_030232_772034`, selected targets [1,2], native
window visible, both camera feeds fresh and advancing. Original environment
and selections preserved.

## Follow-up: redraw the earlier 01:39:57 video

The user requested the same per-user/item count display on the existing
`test_20261028_013957_v6/annotated.mp4`. Completed as a presentation-only redraw
in `D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261028_013957_user_counts`;
the original video and all files in the original result directory remain unchanged.

Clean frames were read sequentially from
`D:/surveillance_video_raw/2026_10_28_01_39_57.mp4`. All 5,960 source frames decoded,
and all 1,209 recorded samples matched the source PTS. Every reconstructed
decision and every final analyzer report field matched the original run exactly.
Source and original-directory hashes were checked before and after rendering.
Each overlay uses only the report as of that source frame.

No new VLX/pose inference or pixel tracking was performed. Provenance is
`recorded_observation_overlay_render` with `fresh_model_inference: false`.
Copies of the original result and manifest are explicitly named `source_result.json`
and `source_manifest.json`. No production code, policy, thresholds, person IDs,
event times or counts changed; the live dual-camera preview was not touched.

Delivered `annotated.mp4`: 245.6s, 1,228 frames, 3040x1642, H.264, 5fps.
`actions_28s_to_130s.mp4`: 102s, 510 frames in the same format. Both fully decode.
Encoded frames at 0, 54.2, 74.2, 110.2, 122.2 and 245.4s confirm empty initial
statistics, current holding timers, separate user/item counts and persistent
completed/incomplete records without clipped labels. Companion `_meta` contains
the redraw/verification scripts, hashes and review images.

The original 4 pickups, 2 putdowns and 2 incomplete records are preserved,
including the known false-positive dog record and other recognition errors.
This display update does not change the earlier accuracy-failed assessment.
`README.md`, `render_verification.json` and `media_verification.json` describe
the distinction. No additional test-suite run was needed for this media-only
follow-up; runtime code remains the previously verified version.
