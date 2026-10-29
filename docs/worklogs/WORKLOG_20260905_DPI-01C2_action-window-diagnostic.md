# Worklog: DPI-01C.2 cam03 Action-Window Diagnostic

Date: 2026-09-05
Status: `ACTION WINDOWS OBSERVED / SURFACE SEMANTICS REVIEW REQUIRED`

## Scope

This bounded artifact-only follow-up analyzes the existing real SAM3.1
propagation run for the `dpi01b` derived video. It does not re-encode the
source, rerun the full 139-second recording, emit Stage9 behavior events, or
modify `PICK_UP`/`HOLDING`/`PUT_DOWN`, association, or product-track logic.

The physical tabletop polygon is retained as diagnostic evidence only. The
configured `product_display_surface` remains the only formal Runtime Gate.

## Input And Provenance

- Video: `D:\\GJJ\\work\\video\\dpi01b_2026_09_04_16_43_24_1280x720.mp4`
- Video SHA-256: `18b9b727a8a8276cb07a6f5ad6c91e6b8af3976ade52bd4cd1f1352252eb5ad6`
- Coordinate frame: `1280x720`, `time_base=1/12800`
- Propagation range: `source_seq=1700..2599`, `70.04s..106.24s`, `900` frames
- Camera: `cam03`
- Products: `ai_electric_guitar` (AI electric guitar), `ai_guitar` (AI guitar)

The source range was selected from the existing real propagation artifact; no
source frame was replaced and no `frame_index/fps` timestamp was substituted
for PTS.

## Action-Window Result

The white `ai_electric_guitar` is classified from the physical-reference
contact sequence as:

```text
stationary_on_stand : source_seq 1700..1733  (70.04s..71.36s)
just_leaving_stand  : source_seq 1734..1745  (71.40s..71.88s)
held                : source_seq 1746..2315  (71.92s..94.84s)
returned            : source_seq 2316..2599  (94.88s..106.24s)
```

The black `ai_guitar` remains a stationary control for the bounded clip and
does not produce a false departure from bounding-box jitter.

The action evidence is complete as a visual window diagnostic, but it is not
a behavior qualification. During the held interval, the white guitar's
physical-tabletop overlap is `0` while its expanded-region overlap can remain
above the existing `0.50` Runtime threshold (for example `0.7166` at
`source_seq=2150`). This is recorded as a Surface semantics conflict rather
than hidden or corrected by changing the Runtime Gate.

## Artifacts

Output directory:
`D:\\GJJ\\work\\dpi01c3_cam03_dpi01b_action_propagation\\`

- `action_window_diagnostic.json`
- `surface_evidence.json` (schema `dpi-01c2.surface-evidence.v2`)
- `action_window_keyframes.jpg`
- `action_window_keyframes/`
- Existing `real_propagation_overlay.mp4`, `real_propagation_summary.json`,
  and `real_propagation_observations.jsonl`

The artifact records `product_key`, SAM object IDs through the existing
propagation evidence, PTS/source sequence references, state intervals, and
the fact that no behavior events were emitted.

The dual-guitar probe CLI also received a minimal fail-closed argument fix:
negative-point defaults are applied only when the option is omitted, so an
explicit negative point is not duplicated in the real SAM3 Prompt log.

## Verification

Focused command:

```powershell
python -m unittest test.test_dpi01c2_action_windows test.test_dpi01c2_real_propagation
```

Result: `7/7 passed`.

The broader DPI/Stage7C/association/interaction focused command also passed
`155/155` tests. One expected provider-failure log is emitted by an existing
Stage10 failure-path test; the test itself passes.

`python -m compileall -q agent tools test` and `git diff --check` passed.

## Boundary

The current `dpi01b` initialization mapping remains
`PENDING_OPERATOR_MASK_CONFIRMATION` (`mask_confirmation=required`). The
bounded propagation Runtime harness therefore remains diagnostic evidence and
does not establish a new formally confirmed Product Track segment for this
video. A new segment may only be initialized after operator confirmation, and
only after any prior segment is formally `LOST`.

Qualification state remains:

```text
REAL_SAM3_MULTI_OBJECT_PROPAGATION_PASS
ACTION_WINDOWS_OBSERVED
SURFACE_SEMANTICS_REVIEW_REQUIRED
FULL_BEHAVIOR_REPLAY_PENDING
NOT_FULLY_CLOSED
```

Do not record `PICK_UP_PASS`, `HOLDING_PASS`, `PUT_DOWN_PASS`,
`FULL_BEHAVIOR_LOOP_PASS`, or `PRODUCTION_VALIDATED`.
