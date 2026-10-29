# cam03 New Sample Recording

Current result: September 6's six behavior events remain anonymous. Three current
member mappings have historical revisions effective September 7, 10:59:25 local.
Do not record a final acceptance sample until the prerequisites below are checked.

September 7 follow-up: the profile loader and existing Runtime geometry parameter
wiring have been fixed. See `reports/cam03_20260907_profile_reuse_audit/REUSE_AUDIT.md`.
Indexed archived calibration is real but cam02-specific; no applicable cam03
package was located. The transform/association/aggregation/projection algorithms
already exist and were not rebuilt. Do not interpret a missing profile path as
an unimplemented identity module. The subsequent minimal CLI integration is now
implemented and has run against the real inbox: see
`WORKLOG_20260907_cam03-cli-identity-integration.md` in `docs/worklogs/`.
Diagnostic collection does not require first completing formal qualification.

## Before Recording

1. Start the receiver below in a dedicated PowerShell. The last check found no
   listener on 8081. Keep it running through the recording and at least one
   accepted heartbeat after the end. Verify Coverage, not just the presence of
   individual Recognition records.
2. Verify member authorization/synchronization in the existing member API and
   verify the current mapping with the bootstrap command. Reauthorization must
   use the member's already authorized photo and existing registration workflow.
3. Establish current effective device-store, camera-store and device-camera
   registrations using existing registration mechanisms. The confirmed local
   configuration is cam03 / store-1 / device cam03; current DB registrations are
   absent. Do not backdate them to the old video.
4. For initial integration, reuse the saved face coordinates, device capture
   metadata and original video first. The existing transform accepts different
   resolutions; do not require cam02's orientation or resolution. The new CLI
   exports three nearest/support bbox overlays. Request a specific additional
   image or parameter only if these reveal an unresolved crop/padding/rotation
   or position problem. No new calibration system or default BackgroundImage
   recapture is required for diagnostics.
5. For a qualified (non-diagnostic) run, load the applicable cam03 profile through
   `RETAILVISION_CAPABILITY_PROFILE_PATH`. Preserve existing formal checks. The
   cam02 fingerprint script must not be renamed into a verified cam03 profile.

## Receiver Startup

Latest September 7 update: the receiver has started successfully. The user then
explicitly requested 0.6 for this onsite receiver. Its active restart command is:

```powershell
& D:/GJJ/work/code/scripts/start_cam03_push_receiver.ps1 -SimilarityThreshold 0.6
```

Do not run a duplicate while 8081 is listening. The default without the explicit
parameter remains 0.75. Fresh face preflight uses 0.6 for this test; confirmed
identity still requires all existing gates. The separate saved-Run identity
diagnostic policy remains 0.75 until explicitly configured for that future Run.
This update supersedes the pre-start status and unchanged-threshold instruction
below for the current test only; see `WORKLOG_20260907_cam03-live-threshold-060.md`.

September 7 onsite preparation: the safe existing-schema receiver launcher is
ready, but the agent's attempted automatic background start was rejected by tool
execution policy. Port 8081 was confirmed not listening. Current mappings were
checked on-device and in immutable history: all three resolve now. Do **not**
treat the old ignored Recognition as this new member's preflight result.

For this onsite artifact-only task, run this in a dedicated PowerShell and keep
the window open:

```powershell
& D:/GJJ/work/code/scripts/start_cam03_push_receiver.ps1
```

It reuses the persisted cam03 fingerprint without printing the serial or asking
for credentials, loads the existing camera JSON, and starts the existing member
API receiver with schema-readiness-only startup. Only inbox/health/Coverage
writes are allowed in this process; migration and BehaviorEvent writes are
rejected. It does not modify PEAK_ENTRY_CAMERA_ID. Stop with Ctrl+C after the
final Coverage heartbeat. This replaces the general-service startup below **for
this no-migration/no-business-write onsite test**.

This command changes only this PowerShell process and its child. It does not
modify PEAK_ENTRY_CAMERA_ID or a global environment. Credentials are prompted,
not persisted. An empty password is supported. Use the already supplied cam03
serial/Unique Code at the prompt. Do not paste secrets into shared reports.

```powershell
Set-Location D:/GJJ/work/code
$env:DATABASE_URL = 'sqlite:///D:/GJJ/work/code/demo_flow.db'
$env:RETAILVISION_CAMERA_CONFIG_PATH = 'D:/GJJ/work/code/configs/behavior_cameras.cam03.identity.json'
$env:BEHAVIOR_CONFIG_PATH = $env:RETAILVISION_CAMERA_CONFIG_PATH
$env:CAMERA_FACE_DEVICE_ID = 'cam03'
$env:CAMERA_FACE_BASE_URL = 'http://192.0.2.4'
$env:CAMERA_FACE_USERNAME = Read-Host 'cam03 device username'
$secret = Read-Host 'cam03 device password (Enter for empty)' -AsSecureString
$env:CAMERA_FACE_PASSWORD = [System.Net.NetworkCredential]::new('', $secret).Password
$env:CAMERA_FACE_UNIQUE_CODE = Read-Host 'Confirmed cam03 Unique Code'
$env:P6S_DEVICE_SERIAL = $env:CAMERA_FACE_UNIQUE_CODE
$env:CAMERA_FACE_GROUP_ID = '0'
$env:CAMERA_FACE_GROUP_UUID = '6f23199d51dc4786bc5fd4893c9fc856'
$env:P6S_SIMILARITY_THRESHOLD = '0.75'
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe -m uvicorn agent.member_api:app --host 0.0.0.0 --port 8081
```

Both camera-config variables deliberately select the same file. Existing offline
loader priority is explicit CLI path, then RETAILVISION_CAMERA_CONFIG_PATH, then
BEHAVIOR_CONFIG_PATH. Settings uses process environment, not an assumed .env.
The JSON's source field still identifies the earlier frozen clip; new capture
uses the explicit `--video` path and must not change old identity runner inputs.

Read-only current member check, in a shell with the device credentials above:

```powershell
$check = 'D:/GJJ/work/code/reports/cam03_mapping_check_' + (Get-Date -Format yyyyMMdd_HHmmss) + '.json'
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe D:/GJJ/work/code/scripts/bootstrap_cam03_member_history.py --camera-config D:/GJJ/work/code/configs/behavior_cameras.cam03.identity.json --database D:/GJJ/work/code/demo_flow.db --output $check
```

`--apply-current` is only needed to start a verified missing current revision.
It has no historical effective-time argument. Existing revisions are preserved.

## Record and Analyze

For the first short onsite sample, use **one member only**. First start the
receiver and have that member face cam03. Confirm a NEW Recognition is accepted
by the unchanged 0.75 policy and resolves at its Event Time before starting the
pickup test. If rejected, report the exact acceptance/history reason, do not
lower the threshold. Then record: face camera -> pick up one product -> hold ->
put back. Capture the recording start/end OSD and the same device clock/Push
event identifiers; do not use the filename as clock evidence. Keep at least
five seconds on either side of the identity event and retain end-heartbeat
Coverage. Return the new MP4 plus the observed clock reference. Event counts and
Person IDs come only from the new Run, never from the old six-event sample.

Have members enter separately, face cam03 for recognition, then each pick up,
hold for several seconds, and replace both guitars in turn. Leave several
seconds at both recording boundaries. Keep both people visible; do not infer
that simultaneous people have the same identity. Record the actual displayed
start clock plus time evidence; the filename is not a trusted start time.

Save the new MP4 and run the existing source-bound two-product initialization
review for that new file. Do not reuse old masks or mark unseen masks confirmed.
Once that produces the new source's reviewed config, confirmation and directory,
the capture entry below saves all observations and events from ONE independent
Run. Prompts request actual paths, not invented values.

```powershell
$video = Read-Host 'New MP4 absolute path'
$productConfig = Read-Host 'New source reviewed product-camera JSON'
$confirmation = Read-Host 'New source initialization_confirmation.json'
$initDir = Read-Host 'New source initialization evidence directory'
$localStart = Read-Host 'Confirmed local start in ISO format, including +08:00'
$timeSource = Read-Host 'Recording time confirmation evidence/reference'
$runOutput = 'D:/GJJ/work/code/reports/cam03_run_' + (Get-Date -Format yyyyMMdd_HHmmss)
& C:/Users/Administrator/miniconda3/envs/sam3/python.exe D:/GJJ/work/code/scripts/run_cam03_evidence_capture.py --video $video --output $runOutput --analyzer-root D:/GJJ/work/retailvision_cam03_probe_20260906_b45 --product-config $productConfig --initialization-confirmation $confirmation --initialization-dir $initDir --store-id store-1 --local-start $localStart --time-source $timeSource
```

Outputs include `person_tracks.jsonl`, `product_tracks.jsonl`,
`raw_stage9_events.jsonl`, `source_run_manifest.json`, overlay and SHA-256 manifest.
New captures also retain `source_timeline_evidence.json`: original ffprobe PTS
AND actual frame durations, scoped to source hash and Run. The existing sampler
checks continuity without a bypass. Missing durations are not filled using FPS
or neighboring PTS. Identity recomputes the timeline assessment from these
hashed observations; older Runs without the evidence remain timeline-unavailable.
Sampling gaps, PTS integrity and declared absolute start/clock qualification are
reported separately. A long real frame may cross sample targets without proving
PTS corruption; a valid IANA/UTC conversion does not prove device/video clocks
are synchronized.
The entry is artifact-only: it does not itself execute identity or publish facts.
The retained analyzer root is an explicit local dependency; preserve its recorded
export-only patch. Main-branch product algorithms were not migrated.

## Identity CLI (implemented September 7)

Run identity in the main repository's API environment after capture has finished.
This avoids mixing the isolated two-guitar analyzer's Python imports with the
current identity runtime. The existing Guarded pass, spatial associator, Track
aggregator, normalizer and projector are called; no publisher is constructed.
`--diagnostic` explicitly permits the existing unqualified durable-inbox path,
not a profile or geometry override. Without this switch a missing profile blocks.

For the completed independent Run of the September 6 video:

```powershell
Set-Location D:/GJJ/work/code
$runOutput = 'D:/GJJ/work/code/reports/cam03_20260907_cli_integration_run'
$identityOutput = 'D:/GJJ/work/code/reports/cam03_identity_' + (Get-Date -Format yyyyMMdd_HHmmss)
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe D:/GJJ/work/code/scripts/run_cam03_run_identity.py --run-dir $runOutput --output $identityOutput --database D:/GJJ/work/code/demo_flow.db --camera-config D:/GJJ/work/code/configs/behavior_cameras.cam03.identity.json --diagnostic
```

For a new capture, retain its `$runOutput` from the preceding command and use the
same identity CLI. Outputs are local restricted diagnostics and should not be
published as face imagery or ordinary business events. No credentials or global
camera environment changes are needed for durable-inbox read-only access.

Observed September 6 result: complete Coverage, one ignored Recognition below
the unchanged 0.75 threshold, no accepted identity evidence, both independent
Tracks unmatched, six anonymous events. The old video still has no Event-Time
historical mapping; September 7 revisions do not repair that history. The three
overlays broadly align the face with Track 2's head. Their pixel/dimension
comparison is diagnostic, not a verified profile. The video OSD at PTS 40s reads
19:11:38 while the reused absolute start implies 19:11:40; no clock correction or
best-overlap frame substitution was applied.

Never feed these new snapshots into the hardcoded 12-event DPI-01E runner or
old six-event replay. This independent Run has its own tokens and six facts.
Formal scope, time/profile/geometry and publication qualification remain intact.
