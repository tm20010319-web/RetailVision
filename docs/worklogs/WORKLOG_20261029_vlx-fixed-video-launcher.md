# VLX fixed video launcher

Date: 2026-10-29
Status: IMPLEMENTED / SHORT REAL PIPELINE VERIFIED
Role: historical development and validation record; not architecture authority.

The user requested processing future videos without per-video source edits and
confirmed existing object presets plus a Windows double-click interactive entry.

## Changes

Added `start_vlx_video.bat`, `scripts/start_vlx_video.ps1`,
`scripts/run_vlx_video_job.py`, `config/vlx_video_launcher.json`,
`test/test_vlx_video_job.py`, and `docs/VLX录像双击运行说明.md`.
Updated the VLX navigation page and worklog index. No existing production Python,
behavior rules, model weights, database schema, or camera configuration changed.
No source file was deleted or renamed. No new third-party dependency was added.

Presets: three toys (original class IDs 0/1/2), guitar (4), and six original
objects (0–5). Separate subprocesses retain the existing VLX grounding, SAM
tracking / guitar grounding, behavior rendering and acceptance export behavior.
The launcher adds stage logs, safe argv paths, interruption cleanup, process lock,
unique run directories, complete-video decode, fact validation, archive hashes,
and the established deliveries/<video stem>/ destination.

Runtime: `runtime/vlx-video-jobs/<video stem>/<UTC run ID>/`.
Packages include final video/results, facts, raw observations, frames, geometry,
job metadata and review status. Repeated per-window frame hardlinks are excluded.
Logs remain in the job directory. No database writes. Failed stages preserve
evidence and stop publication. All automatic results require visual review.

## Validation

- 123 tests passed across the launcher, export, refinement and behavior tests;
  one pre-existing SQLAlchemy deprecation warning.
- Real subprocess argument/exit handling, launch lock exclusion/release, missing
  input/menu retries, repeated-run preservation, cancellation status and archive
  payload hashes covered by launcher tests.
- PowerShell syntax checked; interactive bootstrap reached the Chinese menu and
  exited through `q`. This was command-level validation, not an Explorer GUI click.
- `--check` passed for installed VLX and SAM environments.
- Fresh inference using a 3-second excerpt of the 17:56:11 recording completed
  all stages, including full FFmpeg decode and verified ZIP/SHA256 publication.
  Run: `runtime/vlx-video-jobs/vlx_launcher_smoke_3s/20261029T043458Z_63117ce8`.
  Output contains 8 sampled frames, three selected objects, zero interactions,
  and zero exported facts. This is a pipeline smoke test, not an action-count
  accuracy test. Its package is in `deliveries/vlx_launcher_smoke_3s/`.
- Hash check against the prior file organization manifest: 211 pre-existing
  top-level agent/scripts/config files remained identical.

## Limits

Full-length fresh recognition, all-preset model inference and general camera
accuracy are not newly qualified. Tracking keeps its existing first-two-second
initialization and overlapping-window requirements; occlusion can still stop
tracking. Default tracking window remains 150. The launcher does not change
rules or invent expected counts to resolve model failures. Multiple objects of
the same target class are not independently identified by these presets.

Environment/config paths remain deployment-specific. Other GPU programs are
not controlled by the launcher's per-project concurrency lock. Previously sent
source ZIPs/bundles have not been rebuilt and do not contain this new entry.
