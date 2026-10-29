# VLX home monitor and eye massager video test

Date: 2026-10-28
Status: VIDEO VERIFIED / ACTION ACCURACY FAILED

The user authorized applying the existing direct VLX workflow to
`D:/surveillance_video_raw/2026_10_28_18_30_15.mp4`, with home monitor and AI eye
massager labels, anonymous person attribution, pickup/putdown counts and hold
timing. This is inference, not training VLX or the custom two-class YOLO.

Added `scripts/run_vlx_home_items.py`, an isolated entry point. Its process-local
bindings select the two targets, explicitly pass them to the analyzer, and query
VLX separately for each item. Shared toy/guitar targets, behavior rules, live
weights and services remain unchanged. Output stays inside the workspace.

Evidence: `runtime/vlx-home-items/README.md` and subdirectories. Fresh VLX/pose
analysis covered all 4524 source frames and 909 sampled observations over
184.177 seconds. The automatic result is zero pickups and zero putdowns. Visual
source review shows one person lifting both objects around 165.5–165.9 seconds
and carrying them out of view; no putdown is visible. These visual observations
were not inserted into the model events. Both pickups are missed. Anonymous
user numbers are track IDs; this run's user 9 is not a claim of nine operators.

Additional diagnostic attempts preserved separately:

- SAM3.1 semantic masks on full and lower-half images using camera, goggles and
  eye massager prompts. No reliable moving-object localization was obtained.
- A short SAM3.1 propagation window selected automatically from wrist proximity
  and prior joint VLX localization (151.053s seed, 164.497s proximity). Correct
  initial masks did not survive into the pickup interval.
- Point refinement from the model's own initial masks and VLX box corners also
  failed to retain either instance in the action interval. No hand annotations
  or event times were passed to inference.

Do not promote any of these results as working interaction counts. Further
work needs stronger small-object localization/tracking; replaying the same
rules or injecting the reviewed count would not establish accuracy.

Validation: 72 existing interaction/video/tracking/recovery tests pass; explicit
two-target routing checks preserve the original six-target definitions. Full
H.264 output (1280×744, 5 fps, 184.2s) and 15-second action excerpt fully decode
with ffmpeg `-xerror`. These checks establish artifact integrity, not accuracy.
Other GPU work was allowed to finish before loading VLX. No database access,
production release, live-weight replacement or other-thread interruption.
