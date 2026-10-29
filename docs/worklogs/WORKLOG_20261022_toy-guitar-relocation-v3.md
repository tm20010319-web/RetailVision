# Toy/guitar relocation supplement V3

Date: 2026-10-22 (Asia/Shanghai)
Status: `COMPLETED / CURRENT PLACEMENT VERIFIED WITH KNOWN ERRORS`

## Authorized scope

The user explicitly confirmed using
`D:/surveillance_video_raw/2026_10_22_16_11_17.mp4` to supplement toy training
and verify the real-time result. Only the yellow bee plush and white guitar
are classes. Person annotations are outside this preview.

This is the independent YOLO preview/training workflow. It is not VLX,
SAM3 tracking, stable instance re-identification, Offline V1 development,
Provider A enablement, or production qualification.

## Source and annotations

- Source: 800x448, 25 FPS, 308.188 seconds; SHA-256
  `d4f8e88c174a0e5366a890e4506f59763f403764161463396b7776f569258491`.
- Reviewed 103 frames at three-second intervals and 16 supplementary motion
  frames. Unclear/fully occluded targets are excluded rather than invented.
- The new video contributes 70 selected images: 49 train, 9 validation,
  12 test. Unselected temporal gaps are buffers.
- Eight of the training frames are the explicitly reviewed motion supplement.
  Motion frame 9 (109 seconds) was corrected from `[397,118,433,145]` to
  `[396,104,432,131]` after enlarged grid review. The final dataset was rebuilt
  from V2 and the corrected source. Earlier experimental snapshots remain intact.
- A 45-frame live capture adds the rear-stand guitar pose: train 1-18,
  validation 28-31, test 40-45. All 45 frames were reviewed; minimum target
  template correlation was 0.984.
- A second 36-frame warm-decoder live capture adds the diagonal chair guitar
  and upright rear toy: train 1-14, validation 22-24, test 32-36. All 36
  enlarged target crops were reviewed; minimum correlation was 0.9937.
  Manual boxes are toy `[273,131,298,161]`, guitar `[319,120,395,165]`.

Annotation roots are under
`D:/project/RetailVisionDev/runtime/annotations/`:

- `2026_10_22_16_11_17_3s`
- `2026_10_22_16_11_17_motion`
- `2026_10_22_live_rear_guitar`
- `2026_10_22_live_chair_guitar`

Live-source digests explicitly hash the ordered capture manifest and image
hashes. They are not hashes of an unavailable continuous source-video file.

## Preserved boundaries and evaluation

`tools/training/train_toy_guitar_v3.py` preserves all 323 V2 images, labels,
and split assignments. Configurations, the V2 checkpoint, its split manifest,
and the staff-vest artifacts are hash-checked for unintended changes.

All training comparisons use confidence 0.25 and matching IoU 0.5. The 49 old test
images, 12 new-video temporal test frames and live temporal blocks are
reported separately. No test sample was reassigned to training in response
to a miss. The separately audited old missing guitar annotation is evaluated
through its existing reference; the frozen dataset is not rewritten.

The new blocks have scene/static-frame similarity. Repeated experiments use
the same development holdouts, so these results are not independent-video,
arbitrary-location, all-motion, or production accuracy estimates.

## Experiments and retained failures

Run roots are under `runs/toy_guitar/`.

| Run | Result |
| --- | --- |
| `relocation_v3_20261022` | 100 epochs; 393 images, 706 boxes. New-video test toy 12/12 vs V2 7/12 (and V2 4 false positives); guitar 12/12. Original frozen labels: toy 49/49, guitar 38/38, no false positives. The old audited extra guitar remains missed. |
| First V3 live 30-second observation | Toy present in 253/253 observed inference frames; rear guitar absent in 253/253. This is a retained live failure, not a successful two-object acceptance. |
| `relocation_v3_refined_20261022` | 27 epochs with automatic optimizer; 421 images, 762 boxes. Rear-guitar live temporal holdout still 0/6. Not selected for the viewer. |
| `relocation_v3_final_20261022` | 100 epochs, AdamW lr0 0.0002; 421 images, 762 boxes. New-video toy and guitar 12/12 each, live rear block 6/6 each, no false positives in those blocks. Old frozen: toy 49 TP/1 FP/0 FN, guitar 37 TP/1 FP/1 FN; audited guitar adds another FN. A genuine old dynamic-guitar regression remains. |
| `relocation_v3_chair_20261022` | Selected checkpoint. 100 epochs, 380.34 seconds; 443 images, 806 boxes (305 train / 66 validation / 72 test). New-video toy and guitar 12/12 each, rear live block 6/6 each, chair live block 5/5 each, with no false positives in these blocks. Old frozen: toy 49 TP/0 FP/0 FN, guitar 37 TP/1 FP/1 FN; audited guitar has 2 FN. |

The final-run checkpoint SHA-256 is
`4e110c15bfef4f593ab399d7e5f206cf17d2dea26e95cfa4b02809ace8299e57`.
It detects both objects in the original rear-guitar reference with IoU
0.9395 (toy), 0.9460 (guitar). A newly observed diagonal chair guitar was still
missed; the rear toy had an overly tall box. This was reproduced by
`D:/project/RetailVisionDev/runtime/toy-guitar-live-camera1-v3/check_current_placement.py`,
which asserts both classes match at fixed confidence and IoU. Raising input
resolution to 960 still yielded no guitar candidate even at confidence 0.01.

The selected chair-run checkpoint SHA-256 is
`a5843bcd2d4d3a748351e8b9c8928a6c674a278555c41699d27eae0e68e98cde`.
All 23 new test images (12 source-video, 6 rear, 5 chair) were visually
reviewed in `holdout_review_01.jpg`, `holdout_review_09.jpg`, and
`holdout_review_17.jpg`. The pinned chair-placement regression now passes:
toy IoU 0.84265, guitar IoU 0.87996. The previous checkpoint's failure and
this recovery remain separate artifacts.

The remaining frozen dynamic-guitar failure in `multipose_frame_0056.jpg`
does have a guitar detection at confidence 0.732. Its box IoU is 0.492788,
below the fixed 0.5 criterion; this is inadequate localization, not an
absence of every guitar detection. See `remaining_frozen_misses.json`.
No threshold was relaxed to convert the failure into a pass. All protected
file hashes remain unchanged.

## Video input anomaly

Around 17:02 local time, fresh live images became predominantly magenta and
the model falsely boxed chairs. Independent warm OpenCV and bundled FFmpeg
CPU decodes reproduce it; both SUB and MAIN camera streams have about 62%
high-red/high-blue, low-green pixels. The anomaly precedes model inference.
The cause inside the device/its image pipeline has not been established.

Saved evidence in `relocation_v3_final_20261022`:
`ffmpeg_sub_color.jpg`, `ffmpeg_main_color.jpg`, `stream_color_probe.json`.
One initial gray warm-up frame is explicitly invalid and excluded; it is not
used as a detection failure or a training sample.

The standalone viewer now supports an optional `maximum_magenta_fraction`
setting, enabled at 0.5 only for the V3 preview. It visibly marks this specific
input failure, suppresses AI predictions on such frames, saves an explicit
`invalid_camera_input` state, and resumes when normal frames return. This is
a narrow observed-failure check, not a general image-quality guarantee.
Actual saved normal and failed frames pass the new regression check.
Normal camera colors returned by approximately 17:05 without a device
configuration change; interrupted intervals remain part of the evidence.
The color guard was checked against saved genuine failed/normal input and
tests; the final live observation occurred after colors had recovered and
does not establish that the red warning was observed live during the fault.

## Windows snapshot publication failure

A viewer restart exposed `PermissionError [WinError 5]` while replacing
`status.tmp.json` with `status.json`: a concurrent Windows reader temporarily
held a handle that blocked replacement. A real Windows file-handle regression
test reproduced the crash before the fix. `replace_snapshot` now attempts
replacement up to three times for Windows sharing/access errors (5/32/33), then skips a busy
snapshot while inference continues. Other errors still propagate. The same
publication helper protects JSON and `latest.jpg`. The regression passes;
the subsequent 30-second observation repeatedly read status while the viewer
continued running.

## Runtime and artifacts

The new launcher uses the relocated adjacent Python environment:
`D:/project/RetailVisionDev/envs/agents-py311/python.exe`.
Use `scripts/start_toy_guitar_live_v3.ps1` for the OpenCV preview window.
The runtime directory is
`D:/project/RetailVisionDev/runtime/toy-guitar-live-camera1-v3`.
`status.json` now records the actual loaded weight path and SHA-256.

The final viewer is PID 32760, started at 17:10:11 local time, with window
title `Behavior-1 | Yellow Bee & White Guitar | Q to close`. The V3 live
configuration points to the selected chair-run checkpoint, and its actual
loaded SHA-256 was verified from fresh runtime status.

Training summaries retain their original metrics. Separate
`relocation_v3_chair_20261022/live_validation_summary.json` records the live
evidence and scope. The 30-second observation at 17:11:00-17:11:30 recorded
255 unique fresh inference states: toy present 255/255, guitar present
255/255, exactly one of each in 254/255, mean inference rate 8.48 FPS,
and zero invalid-camera-input observations. Maximum local receive-to-inference
frame age was 0.172 seconds, not camera-to-screen latency.

The remaining state, 21.531 seconds into the observation, has an extra guitar
box at `[3.615,191.619,104.522,249.771]`, confidence 0.428, spatially distinct
from the correctly located chair guitar. Its exact image was not captured;
the six five-second screenshots are insufficient to establish the precise
misidentified pixels. See runtime `validation_chair_30s/extra_detection_details.json`.
All six screenshots were visually reviewed; both targets were boxed at the
current stationary placement. These state counts are observations, not
ground-truth recognition accuracy or proof of arbitrary-placement/motion
performance. The isolated extra detection is retained as a known error.

An AI-annotated replay exists for the first V3, intermediate final and
selected chair experiments:
`2026_10_22_replay_ai_5fps.mp4`, 1541 H.264 frames, 800x480, 5 FPS,
308.2 seconds. The source includes training samples; this is an effects
replay, not an independent test. The selected replay was checked with
ffprobe (`replay_media_verification.json`): all 1541 frames decode, duration
308.200 seconds. Spot checks at 30, 108, 109, 110, 240 and 300 seconds show
the toy in different positions, the toy being moved, and the guitar being
carried. See `replay_movement_review.jpg/json`; these six spot checks do not
constitute complete movement accuracy evaluation. Red face/area boxes visible
in the source are camera/video overlays; this preview predicts only the two
item classes and does not add person detections.

## Verification

Focused suite:
`python -B -m pytest test/test_toy_guitar_live.py test/test_toy_guitar_training.py test/test_toy_guitar_training_v2.py test/test_toy_guitar_training_v3.py -q`

20 passed, 5 subtests passed, one existing SQLAlchemy deprecation warning.
The new tests cover frozen-data integrity, rejected unreviewable images,
duplicate source timestamps, the optional magenta-input guard, and actual
Windows file-handle contention during atomic snapshot publication.

Supplemental training, candidate selection, pinned-placement regression,
30-second live observation, final replay and visual review are complete.
The viewer remains available for the user's placement testing. This result
does not qualify all motion/occlusion cases, stable instance IDs, or VLX.
Old guitar localization misses and the isolated live extra box remain open
limitations of the selected development model.

## 18:09 current-view recovery

The user's follow-up asked about the current result. The freshness check
found PID 32760 had failed at 17:33:31, rather than treating its cached image
as live. The saved traceback reports NumPy failing to allocate a 1.03 MiB
frame copy in `LatestCamera.get`. The underlying memory-pressure cause has
not been established or fixed. No other Python process was stopped.

The same authorized viewer/configuration was restarted at 18:09:06 as
PID 57948; no model, thresholds, camera settings or source code changed.
At 18:09:22 a fresh-state check passed (status age 0.059 seconds), both items
were detected once, and the short initial inference rate was 8.2 FPS.
The preview window title was verified. This establishes recovery, not a
long-run memory fix. Original failure status/log/image and recovery evidence
are under runtime `restart_diagnosis_20261022_1808/`.

## 18:20 user-requested live threshold adjustment

The user reported other yellow/white objects being mistaken for the targets,
then explicitly selected raising the live confidence from 0.25 to 0.50.
Only `config/toy_guitar_live_v3.yaml` was adjusted. Training/evaluation
thresholds and earlier comparison/replay results remain at 0.25; the earlier
30-second counts do not describe the new 0.50 setting.

The previous viewer PID 57948 stopped through its normal `stop.request`
mechanism. PID 52132 started at 18:19:46 using the edited configuration and
the same chair checkpoint. Before restart the saved state contained two toy
boxes at confidence 0.3996 and 0.3047. In a 10.06-second post-restart
observation at 18:20, 88 unique fresh inference states had no detections;
mean rate was 8.64 FPS. Both the latest state and visible snapshot show zero
item boxes. This confirms low-score filtering and resumed video, not correct
recognition of true targets or a measured precision improvement. Higher
confidence can also reject real targets.

Before/after configuration, state and image evidence plus the observation
summary are in runtime `threshold_050_20261022_1819/`. The YAML was parsed
and the live observation checked actual checkpoint identity, frame freshness
and absence of below-threshold outputs. This configuration-only change did
not add code or rerun the previously passing code test suite.
