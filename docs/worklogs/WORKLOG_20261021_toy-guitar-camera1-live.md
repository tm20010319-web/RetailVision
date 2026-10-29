# Behavior-detection-1 toy/guitar live preview

Date: 2026-10-21
Status: `LIVE PREVIEW RUNNING / RELOCATION NOT VERIFIED`

## User-confirmed scope

Recognize only the yellow bee plush toy and white guitar, using the camera
corresponding to the training video: EasyVMS `行为检测-1`. User requested
switching to this camera rather than the previously configured `行为检测-3`.

## Camera identity and source

Read-only inspection of the existing EasyVMS device records and UI identified
`行为检测-1`, P2P prefix `REPLACE_WITH_DEVICE_P2P_PREFIX`, MAC suffix `BF:B7`.
The existing saved device record confirms its current empty-password account.
No encrypted database was modified or decrypted; local logs/UI supplied the
needed device metadata.

Authenticated read-only GETs against `192.0.2.6` verified the matching P2P
prefix and device serial `I20F11AB92E67AA9FE29D3D`, firmware
`3.4.0-20260430-EN`. Device stream configuration reports old RTSP host
`192.0.2.9`; the same stream path is actually reachable at the verified
current host `192.0.2.6`. No device configuration was changed.

Verified TCP RTSP substream: port 554, `/live/0/SUB`, H.265, 800x448, 25 FPS.
This device identity check is for the standalone preview only; it does not
alter or qualify formal P6S identity/publication/capability contracts.

## Independent files

- `config/toy_guitar_live.yaml`
- `scripts/run_toy_guitar_live.py`
- `scripts/start_toy_guitar_live.ps1`
- `docs/TOY_GUITAR_LIVE.md`
- `test/test_toy_guitar_live.py`

The existing two-class checkpoint is unchanged. Training class 0 remains
`toy`; the live display/semantic mapping is explicitly
`黄色小蜜蜂玩偶` / `yellow_bee_toy`. Class 1 is `白色吉他` /
`white_guitar`. No other item is requested or rendered as a target.

The decoder retains one latest frame instead of queueing old frames. The
inference loop targets 10 FPS and rejects frames older than 2 seconds. A stale
source shows a waiting screen; reconnect uses the same verified source.
Current-frame counts are displayed, not cumulative or unique-object counts.

## Actual verification

- Four focused tests pass: camera identity mismatch rejection, explicit
  empty-password configuration, encoded RTSP credential handling, and model
  class mapping verification.
- The real 8-second headless camera probe processed 61 frames at about 9.1 FPS.
  Its final night-vision frame contains toy confidence 0.66 and guitar 0.97;
  confidence is a model score, not measured accuracy.
- The interactive process started successfully and the Computer Use skill
  visually verified its window, both Chinese labels, aligned boxes and one
  count for each target on the actual IR camera view.

Runtime directory: `D:\RetailVisionDev\runtime\toy-guitar-live-camera1`.
`status.json`, `latest.jpg`, `camera_identity.json` and `live.log` are maintained
there. At handoff the process PID is 43468; verify current `status.json` and
process state before future stop/restart actions. Q/Esc/window-close stops it;
a newly written `stop.request` also requests a graceful stop.

No staff-vest configuration/checkpoint, training configuration, SAM3 pipeline,
camera setting, production model binding or business database was changed.
Object relocation and same-instance re-identification remain unverified.
