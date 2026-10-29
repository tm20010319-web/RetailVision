# cam03 Onsite Single-member Preparation

Status: `TIMELINE WIRING FIXED / CURRENT HISTORY READY / RECEIVER START REQUIRED`

This continues the user's onsite verification request, not threshold/product
tuning or an expanded readiness audit. No vision models were run this turn;
old source videos and Run evidence were not modified. No formal BehaviorEvent
was written and no identity was assigned from a candidate or manual source.

## Timeline correction

The diagnosing-bugs regression through `run()` reproduced the reported bug:
missing duration/continuity evidence still passed `presentation_timeline_status`
as `verified`. The failing test observed `['verified']` instead of
`['unavailable']`. The root cause was the literal at the Guarded-pass call site;
an exhaustive hypothesis search was unnecessary for this directly reproduced
defect. The new test now passes and protects that exact seam.

`scripts/run_cam03_evidence_capture.py` now exports the actual ffprobe per-frame
duration together with original PTS, camera/Run/source SHA and stream boundaries
in `source_timeline_evidence.json`. The existing presentation sampler is called
with continuity verification bypass disabled, and actual durations are passed
to the retained analyzer. Durations are no longer invented from adjacent PTS.
No frames, interpolation, padding or timestamp correction is introduced.

`scripts/run_cam03_run_identity.py` verifies that artifact's hash and same-Run
scope, recomputes the result using the existing sampler, and passes that result
to `GuardedOfflineIdentityPass`. It does not trust a saved `verified` string.
Legacy Runs lacking frame-duration evidence remain unavailable at this gate.
Their artifacts are not silently rewritten. Tests distinguish complete PTS with
sampling gaps from actual discontinuity and missing duration. Absolute time
remains explicitly the declared local start converted with ZoneInfo plus PTS;
device/video clock correspondence remains unverified unless independently
established. Correct UTC arithmetic does not prove clock synchronization.

## Real preparation checks

Read-only device mapping command executed successfully:
`scripts/bootstrap_cam03_member_history.py` WITHOUT `--apply-current`.
Current device face-group identities agree with the three existing mappings.
Read-only historical resolver at UTC `2026-09-07T05:37:25.612293` returned:

- member-wudongxin: resolved, current member active, effective from
  `2026-09-07T02:59:25.859196` UTC.
- member-gaojunjie: resolved, current member active, effective from
  `2026-09-07T02:59:25.864707` UTC.
- member-lushun: resolved, current member active, effective from
  `2026-09-07T02:59:25.868708` UTC.

No revisions were created/backdated. Evidence:
`reports/cam03_20260907_onsite_prepare/current_mapping_check.json`.

Host interface is `192.0.2.7`. Port 8081 was not listening. Last persisted
cam03 heartbeat was `2026-09-07T02:06:42.234759` UTC, not fresh. Last recognition
was received `02:05:38` UTC, ignored at 0.24893406; this is NOT the new onsite
member's test, and does not establish the next member's acceptance outcome.

An attempted background receiver start was rejected by the tool execution
policy before execution. A subsequent check confirmed no receiver had started.
No alternative background-launch workaround was attempted. The separate
read-only device mapping check succeeded; live Push cannot yet be claimed.

Existing receiver schema was checked read-only: no missing required columns.
Added `scripts/run_cam03_push_receiver.py`, a process-only launcher that reuses
the actual `agent.member_api` app/lifespan/router/intake and replaces automatic
migration with existing-schema validation. SQLite authorizer permits only
inbox/health/Coverage writes; formal behavior and DDL are rejected. No new
Recognition handler or acceptance algorithm was created. Current global
realtime environment and PEAK_ENTRY_CAMERA_ID were not changed.

`scripts/start_cam03_push_receiver.ps1` prepares only its own process environment
from the existing camera JSON and stored cam03 fingerprint. It contains no
username/password/Unique Code literal. It leaves threshold 0.75 in place and
requires no new operator-supplied camera parameter. The script passed PowerShell
syntax parsing; automatic real startup did NOT pass due to the execution-policy
block above. The existing receiver lifespan and database write restrictions
passed a disposable-database integration test.

## Current five-stage result and stop point

1. New Recognition acceptance: **not executed**, receiver not started and no new
   member face test yet. Do not substitute a stale ignored record.
2. Current historical mapping: **resolved**, all three existing synced members.
3. Face-to-operating-person association: **not executed**, no new recording/Run.
4. Track identity aggregation: **not executed**, no Track ID assumed.
5. Confirmed member behavior: **0 produced**, because no new Run executed.

Stop point is **before live Recognition preflight**. The remaining real cam03
profile path (`RETAILVISION_CAPABILITY_PROFILE_PATH`, profile ID
`p6s_cam03_offline_identity`) and qualified face geometry are still absent. A
diagnostic overlay must not substitute for them or mark identity confirmed.
No new calibration system or default BackgroundImage recapture was requested.

Only next operator actions: run the prepared foreground receiver command; have
one registered member face cam03 and notify the assistant. Confirm acceptance
and Event-Time mapping first. Then record one short face/pick-up/hold/put-back
sequence, retaining clock/OSD evidence and the new MP4. Use existing initialization,
capture and identity CLIs with that new source's own Run tokens and facts.

Commands and short procedure: `docs/cam03_new_sample_recording.md`.
Machine-readable stop state: `reports/cam03_20260907_onsite_prepare/onsite_preparation.json`.

Verification: **204 tests passed, 22 subtests passed**, two existing deprecation
warnings. The red-to-green timeline regression, real disposable receiver
lifespan/authorizer checks and prior member/identity/media/behavior suites were
run. PowerShell launcher syntax passed without starting the service. The narrow
preparation artifacts and changed source/test files are covered by the adjacent
SHA-256 manifest. No full-repository or real onsite sample PASS is claimed.
