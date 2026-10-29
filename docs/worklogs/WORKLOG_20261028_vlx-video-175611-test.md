# VLX three-object uploaded-video test

Date: 2026-10-28
Status: TEST COMPLETE / VIDEO VERIFIED / BEHAVIOR ACCURACY FAILED
Role: historical validation and continuity; not architecture or business authority.

The user requested processing `D:/surveillance_video_raw/2026_10_28_17_56_11.mp4`
like prior annotated videos, and confirmed orange cartoon, white egg and pink
plush as the three fixed instances. No database, camera, frontend, identity,
package migration or delivery-archive change was requested.

## Evidence and processing

Fresh VLX/pose inference covered 185.007 source seconds and 912 samples. Its
immutable output is under
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261028_175611_v1_grounding`.
Source review was sequentially decoded and aligned to source frame PTS; never
use random OpenCV seeking on this VFR HEVC source for exact comparisons.

The first SAM refinement (150-sample windows) failed at window 600 because the
white egg was occluded through the overlap. A 300-sample retry failed at window
750 because the raw white-egg seed overlapped the pink object. Failed runs are
preserved as `test_20261028_175611_v1` and `_v2` in the external runtime directory.

Final output is in
`D:/project/RealtimeEntryPipeline-master/runtime/vlx-interaction-video/test_20261028_175611_v3`.
It reuses prepared frames and this task's fresh VLX/pose evidence, then runs fresh
SAM3.1 tracking in a single 1000-sample-capacity window covering all 912 samples.
This diagnostic window is not a new production default; it approached the 16 GB
GPU memory limit. Render metadata truthfully records reuse of VLX observations.

Final H.264 video: 1760×922, 5 fps, 185.2 seconds, 926 frames; full FFmpeg decode
passes. SHA256: `7449b6ae402a5307420ab90e5fb49b25553cd901dc085263b0b6ac46328bb164`.
Reviewed source contacts and rendered samples at 68, 76, 102, 140, 152, 184 seconds.

## Accuracy outcome

Program output is 4 pickups / 4 putdowns, but is NOT accepted behavior evidence:

- User 1 / orange: 67.725–173.566, 105.841 seconds. Actual visible return is near
  100–101 seconds; a missing wrist and changed resting location delay release.
- User 2 / pink: 67.125–151.564, 84.439 seconds. Earlier brief lift near 62–64
  seconds is not recorded; return of the longer interaction looks near 147–149.
  Whether the early motion is a distinct completed interaction needs finer labels.
- User 3 / white: 65.724–73.126 and 107.728–114.128, 7.402 / 6.400 seconds. Visible
  continuous holding extends approximately to 134–136 seconds. Tiny visible mask
  extents and wrist proximity cause false release/re-pickup; later masks overlap
  the pink item. These are still unresolved.

Manual times above are approximate visual review, not per-frame ground truth.
No manual event insertion, count correction or fabricated detections were used.
See output `测试说明.md` and `validation.json`; results must not be imported as
accepted facts. Object/release/ownership uncertainty needs a separately scoped
repair, with real evidence regressions, before reliable statistics can be claimed.

## Source changes and tests

- `scripts/vlx_mask_refinement.py`: permit observed initialization within the
  first two media seconds, leaving leading rows empty. Later windows still need
  an unambiguous overlap. Render selected targets only, avoiding unused categories.
- `test/test_vlx_refinement_initialization.py`: four regressions through the real
  track entry point using a fake model; startup delay, bound, preferred first
  frame, and overlap constraints. Before fix: 1 failure / 3 pass; after: all pass.
- Related tests: 76 passed, one existing SQLAlchemy deprecation warning. A sandbox
  run first hit pytest temp-directory permissions; approved rerun passed. Encoding
  similarly required approved restart with the local FFmpeg binary on PATH.

The supervisor and media-check helper remain in the clearly diagnostic runtime
directory. Existing application interaction rules, live camera configuration,
database contents, and previously delivered v2 source ZIP/bundle are unchanged.
The v2 delivery does NOT contain this test's source changes. No Git metadata is
available in the working source directory to commit them there.
