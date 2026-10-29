# Worklog: DPI-01C Real SAM3 Adapter And Dense Initialization

Date: 2026-09-04
Status: `CAM03 CONFIG DRAFTED / NO UNOBSTRUCTED FRAME`

## Current State

- DPI-01A initialization domain, fail-closed validation, operator confirmation,
  and atomic Runtime batch creation are implemented.
- DPI-01B preserved the supplied MP4 and produced the verified 1280x720 test
  copy with 3398 decoded frames and monotonic PTS.
- `agent/sam31_real_adapter.py` now adapts the verified SAM3.1 request shape:
  one stateful session, one frame, a tight bbox prompt, then positive and
  neighboring negative point refinement using the single returned `obj_id`.
- `tools/probe_real_sam3_adapter.py` provides a controlled single-product
  probe. It writes frame provenance, call sequence, output shapes, Mask hash,
  and an overlay only when the real predictor returns one valid Mask.
- `tools/calibrate_dense_products.py` can inject that Adapter for the later
  operator-confirmed 3-5 product initialization.
- The Stage5 wrapper's `build_predictor()` currently has no checkpoint
  parameter. A minimal compatibility path now calls its underlying
  `build_sam3_predictor` with the explicit local SAM3.1 checkpoint while
  preserving the wrapper's frozen multiplex/runtime constants.
- Operator confirmation has supplied `logical_camera_id=cam03` and the three
  formal products `ai_electric_guitar`, `ai_guitar`, and `ai_robot`.
- An independent draft configuration was created at
  `product_cameras.cam03.dpi01c.stage7c.json`; it does not modify `cam02`.
- A candidate Surface Overlay was generated from `source_seq=0`,
  `source_pts=0`, and `time_base=1/12800`. The candidate central tabletop is
  visibly intersected by a person's body on the right, so its status remains
  `BLOCKED_NO_UNOBSTRUCTED_FRAME` and it has not been used for SAM3 prompts.
- An offline scan decoded all 3398 frames and found no frame in the candidate
  tabletop region that is free of person/hand occlusion. The lowest marker
  count around `source_seq=797` still shows people over the display, so it is
  not a valid fallback frame.

## API Audit Decision

The installed SAM3.1 Stage7C multiplex path rejects a request that combines
boxes and points. Therefore the Adapter intentionally performs two calls in
the same session and at `frame_index=0`:

```text
bbox prompt -> read the unique returned obj_id
             -> positive/negative point refinement for that obj_id
             -> extract the matching Mask
```

No text prompt is used and no product identity is inferred by the model.
`reference_slot` and `initialization_hint` remain diagnostic-only.

## Blocking Gate

The legacy approved configuration `product_cameras.cam02.stage7c.json`
remains unchanged. It contains only these formal keys:

```text
smart_toy_a
smart_toy_b
```

The confirmed `cam03` set is placed in a new independent initialization
configuration. No real SAM3 prompt is authorized until the new
`product_display_surface` Overlay is accepted by the operator.

## Validation

Focused command:

```text
python -m unittest test.test_product_first_frame_initialization test.test_sam31_real_adapter test.test_stage10_1_realtime test.test_stage10_2_1_product_latency test.test_stage10_2_product_preflight test.test_sam31_product_tracking test.test_person_product_association test.test_person_product_replay test.test_product_interaction test.test_product_interaction_replay
```

Result: `170/170 passed`.

The Adapter and initialization subset after the compatibility change is
`22/22 passed`; `python -m compileall -q agent tools test`, both tool `--help`
checks, and `git diff --check` also pass. The earlier complete focused DPI
suite remains `170/170 passed`. These are code/contract checks only.

## Evidence Boundary

The following claims are intentionally absent:

```text
DPI_01C_REAL_SAM3_ADAPTER_PASS
DENSE_INITIALIZATION_REAL_MASK_PASS
MULTI_PRODUCT_TRACK_INITIALIZATION_PASS
```

No real SAM3 Mask, operator-confirmed Track, business event, or RTSP evidence
from `192.0.2.3` exists in this phase. Do not run the complete 139-second
behavior analysis or modify `PICK_UP/HOLDING/PUT_DOWN` as part of DPI-01C.

## Next Authorized Step

After a replacement recording supplies an actually unobstructed
`product_display_surface` frame, run the single-product probe,
review its Mask Overlay and diagnostic JSON, then run the operator-confirmed
multi-product initializer. Only after all Masks are accepted may one atomic
batch create Product Tracks; Rolling Window continuity is then checked on a
short bounded sample.
