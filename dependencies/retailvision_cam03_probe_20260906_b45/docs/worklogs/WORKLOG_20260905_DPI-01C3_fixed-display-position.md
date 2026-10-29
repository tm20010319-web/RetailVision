# Worklog: DPI-01C.3 Fixed Display Position Semantics

Date: 2026-09-05
Status: `DPI_01C3_FIXED_DISPLAY_POSITION_PASS / READY_FOR_DPI_01D_FULL_BEHAVIOR_REPLAY`

cam03 now declares `display_evidence_mode=fixed_support_contact`. Each guitar
has an approved support point and a normalized tolerance of `0.08`. Runtime
display evidence first applies the existing `product_display_surface` Mask
Gate, then computes the current Mask's median-x lowest-row contact point and
checks its distance to that product's approved point. The approved point never
replaces the dynamic Mask point.

Configured points are `ai_electric_guitar=[793,520]` and
`ai_guitar=[880,528]`. The cam03 calibration status is `CONFIRMED`. Unknown
display modes fail closed; omitted mode remains `mask_surface_overlap` for
cam02 and legacy configurations. Formal Replay additionally requires a
complete current-frame Mask Confirmation manifest. The existing derived-video
The operator has now explicitly confirmed both current-video Masks, their
product-key mappings, the Surface, both support points, and the tolerance. The
confirmation is recorded in
[`DPI-01C3_OPERATOR_CONFIRMATION.json`](DPI-01C3_OPERATOR_CONFIRMATION.json).

The real bounded propagation evidence verifies the white guitar sequence:
`IN_DISPLAY -> OUTSIDE_DISPLAY` after lifting, sustained `OUTSIDE_DISPLAY`
while held, and `IN_DISPLAY` after return to its own support point. A sample
placed at the black guitar support point is outside the white guitar's
tolerance and remains `OUTSIDE_DISPLAY`; small perturbations within tolerance
remain in display. The black guitar remains `IN_DISPLAY`, keeps its Track Token,
and does not exchange identity with the white guitar.

Per-window diagnostics now expose display mode, dynamic and approved support
points, distance, tolerance, Mask Surface overlap, display state, and reason.

Focused tests pass `33/33`; compileall and diff checks pass. Stage9 and full
Person-Product behavior replay were not run.

```text
DPI_01C3_FIXED_DISPLAY_POSITION_PASS
READY_FOR_DPI_01D_FULL_BEHAVIOR_REPLAY
FULL_BEHAVIOR_REPLAY_PENDING
NOT_FULLY_CLOSED
```
