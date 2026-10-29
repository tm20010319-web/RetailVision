# Worklog: DPI-01D Track Loss Recovery Fix

Date: 2026-09-05
Status: `FIX IMPLEMENTED / REAL REPLAY PARTIALLY CLOSED`

## Diagnosis

The white guitar was not returned as a PUT_DOWN because the cam03 replay used
`product_track_lost_after_windows=3`. SAM3 produced no candidate for the guitar
across several rolling windows while it was being held. The Product Track
entered `LOST` at approximately `90.22s`; Stage9 correctly terminates an open
interaction on `LOST` and does not synthesize a PUT_DOWN. Once LOST, the frozen
provider excludes that product from overlap handoff, so the later return could
not recover the original token.

This was not a frame-vs-window counter bug. The runtime increments misses once
per rolling window. The cam03 policy was simply too short for this real hold /
re-detection gap.

## Fix

- Added configuration field `product_track_lost_after_windows` to
  `ProductCameraConfig`, defaulting to `3` for compatibility.
- Set the confirmed cam03 DPI-01D configuration to `16` rolling windows.
- Passed the configured value into the existing `MultiProductTrackRuntime`;
  cam02 defaults and legacy semantics are unchanged.
- Added a regression test proving a 12-window cam03 gap remains recoverable
  with the same Track token and does not create a parallel segment.
- No Stage9, Person-Product Association, member identity, or event semantics
  were changed.

## Real Replay Evidence

Input video SHA-256 remains:

`18b9b727a8a8276cb07a6f5ad6c91e6b8af3976ade52bd4cd1f1352252eb5ad6`

Fixed replay artifact:

`dpi01d_cam03_windowed_replay_recovery16.json`

SHA-256:

`17A8FDD72E965D7EDE0C5AF93D95612F4A38444787C7CD0628F9AD7F8D927B44`

Observed after the fix:

- `ai_electric_guitar`: `PICK_UP/HOLDING 86.81s` -> `PUT_DOWN 97.62s`.
- A later real cycle also occurs: `PICK_UP/HOLDING 111.64s` ->
  `PUT_DOWN 130.22s`.
- `ai_guitar`: `PICK_UP/HOLDING 124.44s`; no return before EOF, so no
  `PUT_DOWN` is emitted.
- Track tokens remain continuous; no new segment is created during recovery.

Diagnostic videos:

- `D:\GJJ\work\dpi01d_failure_samples_fixed\dpi01d_second_electric_cycle_diagnostic.mp4`
- `D:\GJJ\work\dpi01d_failure_samples_fixed\dpi01d_ai_guitar_eof_diagnostic.mp4`

Focused regression suite: `41/41 PASS` for the recovery, readiness, and
product-tracking tests.

The overall DPI-01D acceptance remains pending because the AI吉他 cycle is
still open at EOF. No full behavior or production validation status is
recorded.
