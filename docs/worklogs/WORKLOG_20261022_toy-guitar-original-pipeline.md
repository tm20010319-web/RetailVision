# Toy/guitar original SAM3.1 pipeline integration probe

Date: 2026-10-22
Status: `SHORT REAL-CAMERA PROBE VERIFIED / CONTINUOUS SWITCH NOT COMPLETED`

The user confirmed connection of behavior-detection-1 to the original product
tracking and annotated recording flow. Implemented an independent configuration
and launcher, reusing Sam31RealtimeProviderBundle, MultiProductTrackRuntime,
Stage10DemoRuntime and the original Stage10OverlayRenderer. Original staff-vest,
V1/V2 YOLO training/live configurations and vendor runtime files are unchanged.

Changes:
- `config/toy_guitar_tracking_v2.yaml`, `scripts/run_toy_guitar_tracking.py`,
  `scripts/start_toy_guitar_tracking_v2.ps1`.
- Additive presentation keys `yellow_bee_toy` and `white_guitar`.
- Optional rendered-frame consumer and stop predicate in the original windowed
  Stage10 runner; unchanged behavior when these are omitted.
- Exact first-window YOLO seed; SAM3 semantic initialization fallback when
  night vision makes YOLO seeds missing/ambiguous. Surface/confidence/ambiguity
  checks precede original SAM3 instance propagation. No lowered YOLO threshold.
- Segmented recordings and per-frame metadata; source-time gaps close a segment
  instead of silently joining disjoint camera times.

Real probe captured on host date 2026-10-21 at 23:03–23:04:
`D:\RetailVisionDev\runtime\toy-guitar-tracking-camera1\20261021_230345_501978`.
Three original SAM3 windows produced 28 processed frames, zero runtime errors,
both targets ACTIVE with stable per-product tokens. The three saved MP4s decode
successfully, 12/8/8 frames at 5 FPS. Last source age was approximately 3.64 sec;
this windowed pipeline must not be described as zero-delay live video.

Initial probes exposed keyword-only reader construction, unsupported product
types, and initialization being attached after the original adapter had already
been constructed; those integration errors were corrected. A dedicated semantic
fallback contract test was added. Five focused integration tests pass; the prior
combined Stage10 regression run passed 39 tests before adding the fifth new test.

The user then requested a VLX-based end-to-end experiment. The successful short
probe is retained; a continuous SAM3 viewer was not launched or accepted. The
previous YOLO V2 preview (PID 28384 at inspection) remained the running window.
Do not claim the full live switch or movement acceptance is complete.
