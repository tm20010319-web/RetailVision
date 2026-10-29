# Worklog: DPI-01A Dense Product Initialization

Date: 2026-09-01
Status: `CODE LOOP COMPLETE / UNIT GATE PASS / REAL EVIDENCE PENDING`

## Completion State

- `DPI_01A_CODE_LOOP_COMPLETE`
- `DENSE_INITIALIZATION_UNIT_GATE_PASS`
- `LEGACY_TWO_PRODUCT_BASELINE_PRESERVED`
- `RUNTIME_INITIALIZATION_ATOMICITY_PASS`
- `REAL_SAM3_EVIDENCE_PENDING`
- `REAL_CAMERA_EVIDENCE_PENDING`
- `NOT_FULLY_CLOSED`

This entry does not claim `DENSE_INITIALIZATION_CLOSED`, `REAL_SAM3_PASS`,
`REAL_CAMERA_PASS`, or `PRODUCTION_VALIDATED`.

## Starting Audit

- The current dual-product initialization is owned by
  `Sam31RealtimeProductProvider._select_initial_assignments`, with legacy A/B
  candidate options in `scripts/run_stage10_demo.py`.
- Fixed A/B assumptions existed in the external Stage7C adapter contract,
  Provider Frozen validation, candidate assignment validation, replay loops,
  and CLI options.
- `MultiProductTrackRuntime`, global candidate association, Stage8
  Person-Product Association, and Stage9 Product Interaction already consume
  `product_key` generically.
- The minimum implementation surface was a new initialization domain module,
  an Offline MP4 operator tool, initialization atomicity hardening,
  configuration-driven Provider/replay loops, focused tests, and this worklog.
- Stage8, Stage9, Rolling Window association, Product display semantics,
  behavior events, membership, MCP, database/schema, and camera configuration
  for a new device remained outside scope.

## Implementation

### Initialization domain

`agent/product_first_frame_initialization.py` adds an injected two-phase API:

1. `build_proposal` binds 3-5 approved unique Product keys to one frame hash,
   exact source PTS/time-base, full-frame prompts, one stateful SAM session,
   and unique object/candidate IDs.
2. `commit_confirmed` validates exact frame/mask hashes and an explicit human
   confirmation for every mask before making one Runtime batch call.

The module owns stable failure codes, objective bbox/surface/connected-component
and pairwise-overlap thresholds, ROI-to-full-frame mapping, deterministic mask
hashing, Fake SAM adapter support, and JSON-safe diagnostic payloads. Optional
`catalog_reference_image`, `reference_slot`, and `initialization_hint` values
are diagnostic-only and never participate in Runtime display state.

### Operator tool

`tools/calibrate_dense_products.py` provides an OpenCV Offline-MP4 workflow:

- chooses one decoded frame;
- binds it to `ffprobe` `best_effort_timestamp` and stream `time_base`;
- collects tight bbox, positive points, and neighboring negative points;
- calls an injected `module:factory` SAM adapter in one session;
- shows per-instance mask overlays and requires explicit confirmation;
- atomically initializes Product Tracks; and
- writes the frame, overlay, per-mask PNGs, mapping JSON, and diagnostic JSON
  below `dense_product_initialization/` outside the source tree.

The tool has no RTSP input and refuses non-empty artifact directories. No real
SAM3 adapter was guessed or implemented because the current external adapter's
complete bbox-plus-point API has not been verified.

### Runtime and configuration

- `MultiProductTrackRuntime.initialize_assignments` now validates the entire
  batch before mutating `_tracks` or `_next_segment`, then applies a single
  planned update.
- Provider and Bundle selection are driven by `selected_product_keys`, while
  the existing two-key config remains the default. Frozen validation now reads
  the camera configuration before validating adapter keys.
- Candidate assignments and demo CLI options support repeated generic
  Product-key mappings while preserving legacy A/B options.
- Replay summary loops can receive explicit Product keys instead of relying on
  module-level A/B iteration.

`product_display_surface` remains the only formal display-space region. No
fixed per-product display zone was introduced.

## Validation

### Focused gate

Command:

```text
python -m unittest test.test_product_first_frame_initialization test.test_stage10_1_realtime test.test_stage10_2_1_product_latency test.test_stage10_2_product_preflight test.test_sam31_product_tracking test.test_person_product_association test.test_person_product_replay test.test_product_interaction test.test_product_interaction_replay
```

Result: `167/167 passed`.

The new deterministic suite contributes 19 tests covering dense 3/5 targets,
selection/candidate ordering, all specified proposal and confirmation failure
classes, connected-component noise handling, ROI restoration, exact PTS,
post-proposal Mask mutation, ACTIVE/MISSING/LOST reinitialization rules,
reference-slot non-semantics, atomicity, and Rolling Window token continuity.
Existing association and `PICK_UP / HOLDING / PUT_DOWN` tests remain green.

Additional checks:

```text
python -m compileall -q agent tools test
python tools/calibrate_dense_products.py --help
git diff --check
```

All passed. `git diff --check` reports only the repository's Windows line-ending
conversion warnings.

### Full repository baseline

`python -m unittest discover -s test -p "test_*.py"` ran 1200 tests and ended
with 5 failures in unchanged areas: four
`test_device_recognition_end_to_end` cases and one
`test_person_role_resolver` case. The same 5 failures reproduce when those two
test modules are run alone. They are not treated as DPI-01A regressions or as a
passing repository-wide baseline.

## Atomicity Evidence

The regression starts Product B as `ACTIVE`, snapshots Runtime state, and asks
one batch to initialize new Product A plus Product B. The call fails before
mutation; Product A is absent, Product B's token is unchanged, and every
`_next_segment` value matches the pre-call snapshot.

## Retained Boundaries And Next Step

- The supplied still image remains a human catalog/reference aid only. It was
  not used as an Offline initialization frame, surface calibration, mask, or
  Product Track fact.
- No connection was made to `192.0.2.3`; no RTSP URL or credentials were
  created, and the existing `cam02` configuration was not reassigned.
- No real MP4, real SAM3 adapter, or operator-reviewed mask evidence was
  produced in this phase.

DPI-01B is recommended only as a controlled evidence phase: assign a new
logical camera ID, establish its own 1280x720 surface calibration, record a
formal MP4, implement the real adapter against verified SAM3 APIs, and retain
operator-reviewed artifacts. Stage9 expansion is not part of that step.
