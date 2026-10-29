# Dual-camera VLX preview with one winning view per target

Date: 2026-10-23
Status: LIVE / DUAL-TARGET MEAN 3.44S / INSTANCE IDENTITY AND ACCURACY UNQUALIFIED

## Confirmed scope

User selected EasyVMS `行为检测-1` and `行为检测-3`, with realtime video and
tracking plus periodic shared VLX recognition. Only the original bee plush
and white electric guitar are in test scope. Text selection remains default
empty; no input means no product annotations. When a target has usable results
in both views, only the better view draws its box. Each target chooses its
own winning camera. User accepts an approximate mean three-second interval,
not a hard three-second deadline. Existing YOLO and single-view launchers stay.

## Implementation and boundaries

- New launcher `scripts/start_toy_guitar_vlx_dual.ps1`, entry point
  `scripts/run_toy_guitar_vlx_dual.py`, and `config/toy_guitar_vlx_dual.yaml`.
- Identity-verified substreams: camera 1 at `192.0.2.6`, camera 3 at
  `192.0.2.4`, `/live/0/SUB`, both 800x448. Camera settings were not changed.
- One persistent NF4 VLX model and one persistent official WeDetect model
  serve both streams sequentially. No custom YOLO weights supply candidates.
  `scripts/vlx_dual_support.py` mirrors the pinned official proposal loader's
  constructor/checkpoint remapping and changes its lifetime only.
- CPU optical-flow histories propagate each delayed result within its own
  camera. Camera sequence numbers are never compared across cameras.
- View quality combines available target pixels, normalized crop sharpness,
  tracking appearance/point support and time since confirmation. It is a
  heuristic index out of 100, explicitly NOT a semantic probability or VLX
  confidence. No claim of confidence calibration is made.
- Eight-point switch margin and 0.8-second sustained challenger reduce
  flicker. A missing/expired/failed view cannot retain a winning box; another
  valid view can win immediately. Clearing input clears both views and winners;
  selection revisions reject old in-flight results.
- Multiple disjoint same-class regions in one camera are marked ambiguous
  and excluded, rather than choosing an unrelated larger guitar. This is NOT
  a cross-camera physical-instance identity resolver. A single wrong semantic
  detection can still be wrong; actual instance matching remains unqualified.
- Dual-mode guitar query is `white guitar, upright or horizontal`; it retains
  the provided horizontal crop in an actual model check without a second pass.
  The single-view query/fallback behavior is retained unchanged.
- No Offline V1, SAM3 stages, Provider A, identity/publication/business database,
  production routing or camera calibration contracts changed.

## Evidence

25 focused tests pass in the actual VLX Python environment (existing SQLAlchemy
deprecation warning only). New cases cover independent per-target winners,
switch hysteresis, failed-view fallback, clear, ambiguity, stale/invalid view
rejection, quality degradation with blur/age, selected-target queries, and
rendering a rectangle on the winning panel only. Previous 18 tests remain green.

Actual cached-vs-official WeDetect comparison on the same camera-3 frame:
100 proposals, maximum coordinate difference **0.0**. Raw evidence is under
`D:/project/RetailVisionDev/runtime/toy-guitar-vlx-dual/initial_benchmark_20261023/`.
That run measured bee-only two-view compute around 2.1 seconds. Prior two-pass
dual-target fallback took around 4.3 seconds. Longer merged descriptions took
around 4.6 seconds, so were not selected. Compact query checks and their raw
outputs are retained in `compact_query_check_20261023/`; these are development
checks on supplied/selected frames, not independent accuracy qualification.

Full 40-second-after-load headless dual-target run:
`D:/project/RetailVisionDev/runtime/toy-guitar-vlx-dual/live_dual_check_20261023/`.

| Camera | Recognition frames | Mean inference seconds | Mean completed-confirmation interval |
| --- | ---: | ---: | ---: |
| behavior-detection-1 | 12 | 1.742 | 3.443 seconds |
| behavior-detection-3 | 11 | 1.734 | 3.445 seconds |

Observed confirmation intervals span 3.39--3.58 seconds. This is about 0.44
seconds above the three-second target, not evidence of a strict 3.0-second
result. Both video streams continued independently; terminal display rates
were 17.26 and 9.26 FPS. Camera 3 returned multiple guitar regions in all 11
frames and was excluded for guitar. The bee was recognized on camera 1. This
does not qualify a real same-instance two-view camera-switch scenario.

The visible dual preview is left running, default empty, at the run identified
by `runtime_root/current.json`. The first visible run is
`20261023_105603_572695`. Identity, motion, arbitrary-placement and occlusion
accuracy remain open; user placement tests are still necessary.
