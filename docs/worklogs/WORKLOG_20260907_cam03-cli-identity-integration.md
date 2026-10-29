# cam03 Minimal CLI Identity Integration

Status: `CLI REAL DIAGNOSTIC PASS / COARSE BOX ALIGNMENT OBSERVED / NO MEMBER MATCH`

## Scope and implementation

Followed the current registry/handoff/index and the prior profile-reuse worklog.
This turn follows the user's narrower practical-integration instruction: reuse
cam02 implementation, do not expand readiness/calibration or adjust thresholds.
No readiness script, profile serial/status, camera areas or identity/behavior
algorithm was changed. No new approval or calibration system was added.

Added `scripts/run_cam03_run_identity.py`, a normal CLI consuming one completed
capture Run. It loads the existing camera schema explicitly or through existing
environment priority. Configuration and Provider factory share that parsed
cam03 result. A valid configured profile uses `build_recognition_runtime`;
explicit `--diagnostic` allows the existing unqualified durable-inbox Provider
when only qualification is absent. It never changes a profile to verified.

The entry verifies source/artifact hashes, source_seq/original PTS, UTC-naive
window and exact same-Run Person/Product tokens before querying. It calls
`GuardedOfflineIdentityPass` (existing spatial associator and Track aggregator),
`Stage9BehaviorFactAdapter` and `OfflineBehaviorIdentityProjector`. Raw events
remain verbatim/in order inside diagnostic annotations; only the existing
projector supplies member_id. Nonconfirmed candidates cannot supply identity.
The new CLI does not construct a publisher or mark artifacts production-ready.
Missing configuration without diagnostic mode remains fail-closed with named
missing items. Empty history uses a conservative unavailable bound in the
existing resolver; it does not invent a historical bootstrap or revision.

Three nearest/support overlays reuse the existing normalized bbox renderer and
spatial diagnostic. When a qualified transform exists, the existing geometry
validator/mapper is used. Without it, the original normalized bbox is displayed
explicitly as a full-frame-resize hypothesis; no verified Face/geometry object
is fabricated and the illustration is never fed back as accepted identity.

## Real execution and provenance

- Source: `D:/GJJ/work/video/2026_09_06_19_16_00.mp4`.
- SHA-256: `48c1cdbb5d4e5da2c77add261859f5304b8517a302abf3b23f23422fd976051d`.
- Source PTS: `1/90000`, start 0, duration ticks 10188090 = **113.201s**.
- Reused time source: previous video-overlay start, explicitly reused by user;
  `2026-09-06T19:11:00+08:00`, IANA `Asia/Shanghai`.
- Query interval: `[2026-09-06T11:11:00, 2026-09-06T11:12:53.201)` UTC-naive.
- New Run: `cam03-5d7a637146624ae5b03c52bfdd7f58c0`.
- Directory: `reports/cam03_20260907_cli_integration_run/`.

Old Run lacked body observations. Ran the already-implemented capture CLI once
in the retained analyzer root `D:/GJJ/work/retailvision_cam03_probe_20260906_b45`
(commit `b45ed081ee41f5835568ee3b4facc6a97066444c`), using its existing export-only
patch and source-bound initialization. No new initialization approval is claimed.
No tuning/retries of product algorithms occurred. Same-Run result: **844 body
observations, two Person tokens, six raw behavior events**. 554/554 sampled
contexts processed; terminal pending 0. The sampler reports 12 sampling gaps;
no missing frames/PTS were synthesized. Formal continuous-time qualification
is not inferred from processing all selected samples.

New-Run electric-guitar events use Person Track **1**. The diagnostic face at
40s overlaps Track **2**. Tokens are Run-scoped; these IDs must not be replaced
with the old Run's Track IDs. Do not propagate that face identity to Track 1.
The old six-event replay and earlier 12/11-event inputs remain untouched.

Final real identity output: `reports/cam03_20260907_cli_identity_final/`.
The first execution output at `reports/cam03_20260907_cli_identity/` is retained
with its original entry hash. The final execution uses the same saved Run,
not another model run. Each output directory has SHA-256 manifests. Images,
video and identity artifacts are restricted local diagnostics, excluded from
Git by narrow generated-directory rules.

## Configuration and actual identity result

Explicit config: `configs/behavior_cameras.cam03.identity.json`, hash
`46650eaee2bd25173902d04321e8d828d273bcd28845e55ce605dcd2fd33e4d7`.
Logical camera cam03 / store-1 / device cam03 / durable_push_inbox / auth none.
Configured profile reference `p6s_cam03_offline_identity`; profile path is still
not configured. cam02 config, serial-scoped verification and calibration are
preserved. No credentials or PEAK_ENTRY_CAMERA_ID were modified.

The real **DurablePushInboxRecognitionProvider query executed** in diagnostic
mode, against SQLite `mode=ro` plus `PRAGMA query_only=ON`. Push Coverage is
complete. One Recognition record is present, persisted ignored; its unchanged
0.75 acceptance policy rejects similarity **0.66492233**. Accepted evidence: 0.
Both Track 1 and Track 2 are **unmatched in this diagnostic query**; formal
qualified identity remains unavailable. **Confirmed member behavior count 0;
anonymous behavior count 6; formal BehaviorEvent writes 0.**

The first current device-member revisions remain September 7 10:59:25 local,
after the old video. No September 6 historical identity was recovered, inferred
from current names/mappings, or backdated. Lowering the threshold would not
restore missing history; the 0.75/0.65 experiment is preserved, not repeated.

## Frame review (diagnostic, not full qualification)

Face metadata is 1920x1088. Decoded video is 2560x1440. Actual AI input is
1280x720 with resize 0.5/0.5, zero offsets and no capture-export crop, padding,
mirror or rotation. Camera resolutions/orientations were not required to match
cam02. The existing video has baked-in device face rectangles, so no additional
BackgroundImage was requested for this first position check.

Inspected source sequences 944/949/954, PTS **39.8/40.0/40.2s**. All three yellow
inbox-face boxes sit around the central person's head, inside that Run's green
Track 2 body box; no gross crop, rotation or whole-frame position displacement
is visible. At the nearest PTS40 frame, existing spatial diagnostics give
containment **0.8777778**, face center inside and upper-body check true; Track 1
containment is 0.0. This is coarse alignment, **not an identity confirmation**.

The existing geometry diagnostic reports `vertical_padding_unverified` (an
8-pixel *candidate* inferred from aspect ratios). This is not evidence that
actual padding exists and no padding correction was applied. Fine FaceReco
capture geometry is not formally verified from these three pictures alone.

The video OSD reads **19:11:38** at original PTS40, whereas the reused confirmed
start implies **19:11:40**. This is a concrete clock discrepancy for subsequent
formal validation, not an excuse to shift the Recognition time or choose a more
favorable snapshot. No time correction or prior artifact rewrite was made.

## Verification and next use

**199 tests passed, 22 subtests passed**, two existing deprecation warnings.
New entry tests cover the real Guarded association/projector, Track independence,
complete empty vs missing Coverage (real disposable SQLite), partial-query
fail-closed behavior, explicit config over stale cam02 environment, profile
factory dispatch, nonconfirmed aggregation/projection, raw facts unchanged,
bad source/Run/PTS evidence and database writes rejected by the connection.
No full-repository-green or production qualification claim is made.

Reproducible capture/identity commands are in `docs/cam03_new_sample_recording.md`.
For this saved Run only the read-only identity command is needed; do not rerun
SAM again to chase an identity. New recordings should collect Recognition after
the real historical mapping takes effect and retain their own snapshots/events.
Record device/video clock evidence together. The existing qualified profile and
formal publication gates still apply to a future formal run.
