# Product location: environment restoration and camera preparation

Date: 2026-10-21
Status: `CAMERA IDENTITY VERIFIED / SAM3 FRAME PROPOSAL READY / LIVE QUERY NOT IMPLEMENTED`

## User-confirmed scope

- Independent backend module, with only necessary integration changes; no new frontend.
- This delivery covers the EasyVMS device named `行为检测-3` only.
- Target is the specific white guitar selected by the user, not any same-model guitar.
- First initialization may use operator bbox/point selection and mask confirmation.
- Continuous tracking and queries should require no repeated selection while identity remains valid.
- After complete tracking loss, or when runtime continuity cannot be established, operator reinitialization is acceptable. No automatic same-instance re-identification is assumed.
- Location must describe calibrated image subregions, such as `左侧展示架旁` and `蓝色地垫区`. Their actual polygons have not been confirmed.
- Deliver an actual annotated source-frame image as well as a readable location result. The box must correspond to the observation's camera and exact source frame.
- Real camera, movement, loss/reinitialization and annotation validation remain required.

## Restored assets

Bundle: `D:\retailvision-dev-handoff-20261020`.
Install: `D:\RetailVisionDev`.
Code root: `D:\project\RealtimeEntryPipeline-master`.

All four bundle archives passed SHA-256 verification against `SHA256SUMS.txt`.
Inspected archive contents include the SAM3.1 checkpoint
`sam3.1_multiplex.pt`, its config, SAM3 source and custom runtime modules.

Executed the supplied `Restore.ps1` for the current code root. It restored
both environments and external source, registered SAM3 editable source, and
added the five bundled YOLO weights under `data/models/`. Existing application
source was not edited. The environments and generated local configuration are
outside the checkout.

Executed the supplied `Verify.ps1 -LoadModels` successfully:

- Main Python 3.11.15, torch 2.11.0+cu128.
- SAM3 Python 3.12.13, torch 2.10.0+cu128.
- Five YOLO weights completed synthetic CPU inference.
- SAM3.1 checkpoint deserialized with 1623 tensors.
- CUDA matrix computation passed in both environments on RTX 5070 Ti, 16303 MiB, driver 610.88.
- Current and retained external runtime import checks passed.

Evidence is under `D:\RetailVisionDev\runtime\verification\`.
These checks do not establish full SAM3 video inference or real-camera capability.

## Current checkout observations

The desktop implementation plan describes a different physical layout. This
checkout keeps SAM3 files directly under `agent/`; it does not contain the
plan's `agent/product_search/` or `configs/product_search/` framework.
Do not assume those modules exist or migrate the repository to match the plan.

`Sam31RealtimeProductProvider` currently accepts 2–5 targets. The first-frame
policy can represent one target, but a single-target live assembly needs a
separate integration. Existing Stage5/7C/8/9 semantics and thresholds remain
unchanged. A dedicated source-to-query implementation has not been written yet.

Static review confirms operator-confirmed initialization, geometric/mask
association, ambiguous-candidate rejection and tracking loss semantics. The
existing reference-photo field is a human aid rather than implemented automatic
photo matching.

Validation command in the restored main environment:

```text
python -m unittest test.test_product_first_frame_initialization test.test_sam31_product_tracking test.test_sam31_real_adapter -q
```

Result: **60 tests passed**. This is a focused baseline, not a full repository result.

## Camera state and next actions

The screenshot shows EasyVMS P2P access. The user has camera credentials but has
not supplied them to this session. The workstation has WLAN `192.0.2.5/24`.
The historical cam03 HTTP address `192.0.2.4:80` is reachable;
`GET /System/DeviceInfo` returns 401 with Basic realm `IP-Camera`.
Its association with `行为检测-3` remains **unverified**.

Added `scripts/setup_product_location_camera.ps1` for interactive local camera
login and a read-only metadata request. It bypasses local proxies, does not
follow redirects, encrypts successful credentials using Windows Export-Clixml,
and stores only allowlisted metadata in
`D:\RetailVisionDev\runtime\product-location\camera-preflight.json`.
It deliberately leaves `identity_confirmed=false`. PowerShell syntax parsing
passed; its interactive login path has not run.

Next: operator enters device credentials locally; compare device information
with EasyVMS, discover the actual supported stream, obtain a full source frame,
confirm region polygons and target initialization, implement the isolated
single-target query integration, then validate real movement and loss behavior.
No camera settings, business database or publication qualification was changed.

## Credential prompt follow-up

The user reported no visible login prompt. The previous Get-Credential path
left a PowerShell invocation waiting without console output. A non-interactive
reproduction using the script through `-Command` remained silent beyond ten
seconds and was terminated. A temporary subprocess regression also failed the
requirement for an explicit username prompt before the change.

Replaced Get-Credential with visible username Read-Host and hidden password
Read-Host -AsSecureString prompts, added three progress stages and a public
`camera-setup-status.json` containing stage/address/time only, and provided
`scripts/setup_product_location_camera.cmd` as a terminal launcher. Syntax
parsing and the subprocess regression passed: non-interactive invocation now
prints an explicit prompt and fails promptly rather than waiting silently.

Stopped only the identified old setup invocation and opened a new visible
PowerShell session for human credential entry. Its public status advanced to
`waiting_password`, demonstrating that the new username input path was reached.
Successful authentication, device identity and real video are still pending.

## HTTP 401 follow-up

The interactive device request returned HTTP 401 without saving credentials.
The user confirmed the entered account was the EasyVMS software login, not a
confirmed local camera-device account. Device credentials are still required;
the historical IP's association with the selected EasyVMS device is unverified.

A local HTTP Basic fixture exercised the actual setup script using synthetic
credentials and Windows PowerShell 5.1: unauthenticated request -> 401 challenge
-> authenticated request -> 200, metadata report and encrypted fixture
credential saved. A second fixture confirmed rejected credentials produce 401
and no credential file. The 401 diagnostic initially failed a regression
assertion because it misleadingly suggested an interactive PowerShell problem.
The script now gives an account/address-specific diagnostic; both fixture
cases and PowerShell syntax validation pass. Fixture directories were
temporary; no actual camera credentials or settings were used by these tests.

## Confirmed empty-password access and real SAM3 frame proposal

The user explicitly confirmed device account `admin` with an empty password.
A read-only request returned HTTP 200. Added explicit `-DeviceUsername` and
`-UseEmptyPassword` options to the helper and ran it successfully; encrypted
credentials and public metadata are saved outside the checkout.

Verified identity and read-only source access:

- Address `192.0.2.4:80`, model `T5AI-W/IPG-5950PCS-AI`.
- Serial `I20F11AB2E72F1A1D621A3C` matches the existing cam03 profile.
- `GET /Network/P2PV2`, found in the device's own web-client source, has an
  enabled entry whose DID prefix is `REPLACE_WITH_DEVICE_P2P_PREFIX`. This matches the
  user's EasyVMS screenshot and confirms `行为检测-3`. Only the DID prefix is
  recorded in public evidence; extended UUID suffixes must not be propagated.
- `GET /Network/Port` reports RTSP 554, HTTP 80, Command 6060, Media 6066.
- The public preflight report now records the DID verification.
- The web-client's `GET /Snapshot/1/RemoteImageCapture?ImageFormat=2` returns a
  real 2560x1440 JPEG. No remote camera settings were changed.

Source image:
`D:\RetailVisionDev\runtime\product-location\camera138-source-20261021T035728Z.jpg`.
SHA-256: `be24fbf3ce4e899865f213935ee3d91d3192f530c70faa4aec749f908f074b66`.
Server response reception: `2026-10-21T03:57:28.923360+00:00`; camera OSD shows
11:57:28 local. This does not qualify device-clock calibration.

Added `scripts/probe_product_location_target.py`, an isolated saved-frame
diagnostic reusing the unchanged SAM3 bbox-to-points adapter. It records source
SHA-256, original/AI coordinates, prompts, checkpoint, mask and previews. It
does not commit a Product Track, continuously track, or answer current location.

The first proposal included chair background because two points landed on the
chair; it was not accepted. Corrected target points and explicit background
exclusions produced a 3280-pixel guitar mask on the 1280x720 inference frame.
The last two real GPU runs produced identical masks. The last run took 13.485
seconds including model load, not steady-state throughput. Startup logs are
retained separately. Fixed the new diagnostic's log-file lifetime; the last
run has no closed-file logging error.

Latest proposal directory:
`D:\RetailVisionDev\runtime\product-location\white-guitar-proposal-03`.
Contains `proposal.json`, `proposal.jpg`, `proposal-detail.jpg`, and `mask.png`.
Status is `requires_operator_confirmation`. A saved-frame proposal cannot
establish freshness or identity for a future live session. Continuous stream,
region calibration, single-target query/API, movement annotation and
loss/reinitialization acceptance remain unfinished.
