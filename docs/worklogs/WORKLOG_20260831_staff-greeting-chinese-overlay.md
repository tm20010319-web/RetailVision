# Worklog: Staff Greeting Chinese Demo Overlay

Date: 2026-08-31
Status: `COMPLETED / CHINESE OVERLAY VIDEO FINALIZED`

## Scope

Localized the Offline Staff Greeting analyzed-video presentation layer to
Chinese and added the `秒象科技` label to every person detection box. Greeting
Core behavior, model inference, PTS, Artifact contracts, and event results were
not changed.

## Implementation

- Added one-pass Unicode BGR overlay rendering with the existing Pillow
  dependency and a configurable Chinese font path.
- Localized person-role labels, Vest evidence, spatial evidence, Greeting state,
  Offline source/run/calibration metadata, and terminal banners.
- The formal Offline CLI now defaults to `--overlay-language zh-CN` and
  `--overlay-brand 秒象科技`; the underlying reusable classes retain explicit
  English compatibility.
- Brand text is placed below each person box when space permits and moves inside
  the lower box edge only when the frame boundary would crop it.

## Validation

- Focused Greeting/Vision/Offline/realtime-gate suite: `123/123 passed`.
- Controlled CUDA replay used the same source, models, `imgsz=1280`, ByteTrack,
  camera configuration, and verified PTS as P1.1.
- Final run: `05ebc946c163467fb56090fb58179e9d`; `1808/1808` frames at
  `2560x1440`.
- Summary remains `total=2`, `confirmed=1`, `uncertain=0`, `cancelled=1`;
  target confidence remains `0.7185347080230713`.
- Visual keyframes at 66s and 69s confirm Chinese labels and per-box branding.

Source-native content such as the camera's `AI IPC` watermark and text visible
inside the recorded store displays is intentionally unchanged.
