# Worklog: DPI-01C.2 cam03 Real Dual-Product Propagation

Date: 2026-09-04
Status: `REAL PROPAGATION PASS / SURFACE SEMANTICS PENDING`

## Scope

This bounded run closes the real SAM3.1 dual-object propagation gap for the
operator-confirmed `cam03` guitar batch. It does not run the full recording,
produce Stage9 behaviour events, or change `PICK_UP`/`HOLDING`/`PUT_DOWN`,
Person-Product Association, membership, MCP, or RTSP behaviour. The yellow
robot remains deferred.

## Inputs And Configuration

- Video: `D:\\GJJ\\work\\video\\2026_09_04_18_17_36.mp4`
- Video SHA-256: `2d77d07b8f2cc7040ed5218cd0766b19fc9a8561b713498cf1aa1fe212ee2d49`
- Coordinate frame: `1280x720`, `time_base=1/90000`
- Camera: `cam03`
- Selected products: `ai_electric_guitar`, `ai_guitar`
- Formal types: `electric_guitar`, `guitar`
- Tracking compatibility types: `smart_guitar`, `smart_guitar`
- Surface: `product_display_surface`, configured rectangle
  `[(515,300),(980,300),(980,545),(515,545)]`
- Physical tabletop evidence polygon retained separately:
  `[(520,454),(952,452),(968,526),(521,531)]`

The independent configuration is
`product_cameras.cam03.dpi01c.guitars.stage7c.json`; legacy `cam02` remains
unchanged in meaning and scope.

## Evidence Audit

C.1 confirmed artifacts were present and matched the C.1 Worklog:

| product_key | C.1 candidate | C.1 Track token suffix | C.1 Mask SHA-256 |
| --- | ---: | ---: | --- |
| `ai_electric_guitar` | 0 | `ai_electric_guitar:1` | `5fc75b1745ad740c86eb3548a4cd76233261cda2dcc4af34c735bce469198808` |
| `ai_guitar` | 2 | `ai_guitar:1` | `bab2022d7ae5fc37c4acfb12997a8ab2855f8b6bb935c5b38703bfe74231a44c` |

The independent C.2 session re-executed the confirmed same-frame prompts and
then propagated real decoded frames. Its frame-0 propagation Mask hashes were
`ace5819f449993796fa44480f08236854a8e58fdbf5993f4618092c23e40537d` and
`a020a1e415042b9a9da0c11a01effd29e2d7168eb13f2b65ec5ec2ceabcc2204`.
They are recorded as fresh C.2 outputs, not falsely presented as byte-identical
C.1 Masks. Runtime initialization remains bound to the operator-confirmed C.1
mapping and frame SHA.

The same SAM session retained both objects after the second Prompt. The
diagnostic recorded final `active_obj_ids=[0,2]`, SAM2 state IDs `[0,2]`,
`removed_obj_ids=[]`, `suppressed_obj_ids=[]`, and `unconfirmed_obj_ids=[]`.
The second Prompt returned two candidates and did not hide or replace the
first object. The Adapter call log records the backend's repeated bbox
`obj_id=0` and its deterministic selection of the requested refinement object
`2`.

## Real Propagation Result

Command:

```powershell
conda run -n sam3 python tools/probe_dpi01c2_real_propagation.py `
  --checkpoint-path "C:\\Users\\Administrator\\.cache\\huggingface\\hub\\models--facebook--sam3.1\\snapshots\\daa63191845a41281374e725f4c9e51c7a824460\\sam3.1_multiplex.pt"
```

Output directory:
`D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\`

The bounded probe materialized and propagated `121/121` frames. Every frame
returned SAM `obj_id=[0,2]`; there were no unknown IDs, no visual ID swap, no
runtime-gate-hidden objects, and no propagation failure codes. Both bounded
Rolling Windows associated by `overlap` with `association_success=true`,
`association_ambiguous=false`, `track_status=ACTIVE`, and stable tokens:

```text
product:cam03:dpi01c2-cam03-real-propagation-20260904:ai_electric_guitar:1
product:cam03:dpi01c2-cam03-real-propagation-20260904:ai_guitar:1
```

The artifact status is `PASS_PROPAGATION_SURFACE_CONFIRMATION_PENDING`.

## Surface Decision

The generated comparison overlay labels `PHYSICAL_TABLETOP` and
`CURRENT_EXPANDED_REGION`. The bounded clip shows only
`stationary_on_stand`; it does not contain confirmed just-leaving, held, or
returned states. Therefore the surface decision remains
`INSUFFICIENT_MOTION_FOR_SURFACE_SEMANTICS` and calibration remains
`PENDING_OPERATOR_CONFIRMATION`. No threshold was relaxed and no product-level
display zone was restored. The real evidence is insufficient to decide whether
the expanded region would keep a lifted guitar in `IN_DISPLAY`.

## Artifacts

- [real_propagation_summary.json](D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\real_propagation_summary.json)
- [real_propagation_diagnostic.json](D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\real_propagation_diagnostic.json)
- [real_propagation_observations.jsonl](D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\real_propagation_observations.jsonl)
- [real_propagation_overlay.mp4](D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\real_propagation_overlay.mp4)
- [surface_comparison_overlay.jpg](D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\surface_comparison_overlay.jpg)
- [surface_evidence.json](D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\surface_evidence.json)
- [operator_confirmation.json](D:\\GJJ\\work\\dpi01c2_cam03_real_propagation\\operator_confirmation.json)
- C.1 mapping and confirmed Masks: `D:\\GJJ\\work\\dpi01c_cam03_guitars_run\\two_guitars_final\\`

The Overlay keyframes at source sequences `0,1,15,55,85,120` were visually
checked. Labels show both product keys, object IDs, Track token short IDs,
`PROPAGATED/PENDING_ASSOCIATION`, PTS, and both Surface polygons.

## Tests And Checks

Focused command:

```powershell
conda run -n sam3 python -m unittest test.test_product_first_frame_initialization test.test_sam31_real_adapter test.test_dpi01c2_real_propagation test.test_stage10_1_realtime test.test_stage10_2_1_product_latency test.test_stage10_2_product_preflight test.test_sam31_product_tracking test.test_person_product_association test.test_person_product_replay test.test_product_interaction test.test_product_interaction_replay
```

Result: `175/175 passed`.

`conda run -n sam3 python -m compileall -q agent tools test` passed, and
`git diff --check` passed. The `sam3` environment does not include `pytest`; the
repository's unittest command was used as in the existing worklogs.

The full repository discovery was also run in the complete-dependency
`agents-py311` environment: `1213` tests, with the same five pre-existing
failures outside this DPI scope:

```text
test_poll_failure_does_not_stop_the_push_flow
test_poll_then_push_creates_one_canonical_visit_and_profile_job
test_push_then_poll_creates_one_canonical_visit_and_profile_job
test_recognition_upgrades_unique_entry_visit_without_duplicate
test_frame_clipped_person_accepts_lower_visible_torso_vest
```

No DPI-01C2 test failed in that run. The `sam3` environment's separate full
discovery was not used as a baseline because it lacks the repository's web,
HTTP, and YOLO dependencies and consequently reports import errors.

## Boundary And Next Step

Recorded qualification state:

```text
CAM03_SURFACE_SEMANTICS_PENDING
REAL_SAM3_MULTI_OBJECT_PROPAGATION_PASS
SAM_SESSION_TWO_OBJECT_RETENTION_PASS
PRODUCT_KEY_VISUAL_ID_CONTINUITY_PASS
PRODUCT_TRACK_TOKEN_CONTINUITY_PASS
LEGACY_CAM02_BASELINE_PRESERVED
FULL_BEHAVIOR_REPLAY_PENDING
NOT_FULLY_CLOSED
```

Do not record `PICK_UP_PASS`, `HOLDING_PASS`, `PUT_DOWN_PASS`,
`FULL_BEHAVIOR_LOOP_PASS`, or `PRODUCTION_VALIDATED`. Full Offline behaviour
Replay and DPI-01D remain blocked until an operator-confirmed Surface and a
clip containing the required display-state transitions are available.
