# Worklog: DPI-01D Full Dual-Guitar Behavior Replay

Date: 2026-09-05
Status: `REPLAY EXECUTED / ACCEPTANCE BLOCKED`

## Reused Historical Chain

The replay reused the previously qualified Offline `GoldenMp4Analyzer` path:
authoritative PTS sampling at 5 FPS, 12-frame first window, 8 fresh plus 4
overlap frames, independent SAM3 sessions per window, and the existing Stage5,
Stage7C, Stage8, and Stage9 runtimes. It ran in `agents-py311` with CUDA on the
RTX 5070 Ti. The obsolete long-session C.2 probe was not called; it remains in
the repository only as the source of historical C.2 evidence and is not deleted
because existing Worklogs and validation references depend on it.

## Minimal Compatibility Fixes

- Passed the operator-confirmed `product_key -> obj_id` mapping into the frozen
  provider so Offline replay cannot open the legacy interactive `cv2.waitKey`
  selector.
- Used the concrete `guitar` Grounding noun for cam03 rolling windows.
- Reused the existing C.2 empty propagation-cache priming and overlap bbox/point
  handoff for confirmed cam03 prompts only. Legacy cam02 remains unchanged.
- Added presentation labels for `ai_electric_guitar` and `ai_guitar`; no Stage9
  or product-state semantics changed.

## Replay Evidence

Input:

```text
video = D:\\GJJ\\work\\video\\dpi01b_2026_09_04_16_43_24_1280x720.mp4
sha256 = 18b9b727a8a8276cb07a6f5ad6c91e6b8af3976ade52bd4cd1f1352252eb5ad6
camera = cam03
start_source_seq = 1700
analysis_fps = 5.0
sample_count = 342
provider_processed_sample_count = 342
provider_sample_coverage_status = complete
source_pts_status = available
```

Artifact:

```text
dpi01d_cam03_windowed_replay_agents.json
sha256 = aa72c734b5376b9e8a415a81083c618d1140cc045d3c5f6f66df1dccae1bba08
```

Observed events:

```text
ai_electric_guitar:
  PICK_UP   72.22s
  HOLDING   72.22s
  PUT_DOWN  83.03s  holding=10.81s
  PICK_UP   86.81s
  HOLDING   86.81s

ai_guitar:
  PICK_UP   124.44s
  HOLDING   124.44s
```

The first electric-guitar cycle closes correctly. The second electric-guitar
cycle enters `LOST` at approximately 90.02s and has no confirmed return. The
black guitar is picked up at 124.44s and remains without a confirmed return
before EOF. These are fail-closed incomplete cycles, not inferred PUT_DOWN
events.

## Acceptance

Focused regression and compatibility suite: `163/163 PASS`.

The following are deliberately not recorded because the real video evidence
does not contain two closed cycles:

```text
DPI_01D_DUAL_PRODUCT_BEHAVIOR_REPLAY_PASS = NOT RECORDED
PERSON_PRODUCT_ASSOCIATION_PASS = NOT RECORDED
STAGE9_DUAL_PRODUCT_BEHAVIOR_PASS = NOT RECORDED
READY_FOR_DPI_01E_OFFLINE_MEMBER_BINDING = NOT RECORDED
```

Current state remains:

```text
FULL_BEHAVIOR_REPLAY_PENDING
NOT_FULLY_CLOSED
```
