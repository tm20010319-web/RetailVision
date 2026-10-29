# Camera-1 V2 live test

Date: 2026-10-21
Status: `LIVE PREVIEW RUNNING / USER PLACEMENT TEST PENDING`

Following completion of multi-pose V2, the user explicitly requested connection
to the surveillance camera for testing. Continued the established camera
selection `行为检测-1` and enabled the newly trained model for this standalone
preview. Existing V1/staff-vest configurations and camera settings are preserved.

- New configuration: `config/toy_guitar_live_v2.yaml`.
- New convenience launcher: `scripts/start_toy_guitar_live_v2.ps1`.
- Existing preview entry: `scripts/run_toy_guitar_live.py`, unchanged.
- Model: `runs/toy_guitar/multipose_v2_20261021/weights/best.pt`.
- Camera host: `192.0.2.6`; RTSP `/live/0/SUB`, TCP.
- Device P2P identity verified against the existing expected prefix.
- Dedicated runtime: `D:\RetailVisionDev\runtime\toy-guitar-live-camera1-v2`.
- Visible preview process started at 22:33 local, PID 28384, responding window
  `Behavior-1 | Yellow Bee & White Guitar | Q to close`.

Verified status advances through fresh camera frames, approximately 9 FPS,
800×448 source. The viewed live snapshot shows one yellow bee plush (about
0.96 confidence) and one white guitar (about 0.80 confidence), with Chinese
labels. Counts are per-frame detections; no temporal tracker was added.

User can move/rotate the items and watch the preview. Q/Esc/window-close stops
the process; writing a fresh `stop.request` in this dedicated runtime also stops
it. No automatic acceptance of arbitrary placement or continuous tracking is
claimed. V2's previously documented duplicate-box/localization limitations apply.

V1 live configuration SHA-256 remains
`68097f3d9dee27eebb234afed4b583b1e190e570edd63418c4946302efad8e3a`.
