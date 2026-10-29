# VLX missing-box diagnosis and duplicate-region correction

Date: 2026-10-23
Status: DUPLICATE FILTER FIXED / GUITAR DISAMBIGUATION PARTIAL / LATENCY TARGET OPEN

## Report and diagnosis

User supplied dual-view screenshots around local 11:00:07 (bee lying in the
blue display recess) and 11:00:33 (specified white guitar on the display).
The selected text was active. User then explicitly requested continuing.

Runtime raw results from `20261023_105603_572695/results.jsonl` show:

- Camera 1 returned no bee. Camera 3 initially returned no bee either.
  At UTC 03:00:14.750514 and subsequent frames it returned two overlapping
  proposals for the SAME bee: whole-toy and body regions. Their IoU is below
  0.7, so the initial uniqueness filter incorrectly discarded both.
- At UTC 03:00:26.567608 and subsequent frames camera 3 returned two spatially
  separate guitar regions, including the specified guitar. The initial filter
  rejected the class entirely. This is different from a semantic no-detection.
- Thus both genuine model misses and postprocessing rejection contributed.
  The earlier exact input images were not retained; proposal-vs-semantic
  causes for the no-detection frames are not established.

Actual-log replay printed model_boxes=2, display_candidates=0, ambiguous=[0]
and failed its expected-single-bee assertion. A golden regression using those
exact coordinates likewise failed before the repair.

## Changes

- Duplicate merging now accepts high IoU OR body/whole-object containment:
  at least 80% of the smaller region is covered and area ratio is at least
  0.25. The merged box encloses the proposals. Extreme area differences and
  spatially distinct objects remain ambiguous. No fixed camera coordinates
  were added to inference or filtering.
- When multiple distinct guitar candidates remain, run the more specific
  `all-white electric guitar` query on the SAME image and automatic proposals.
  Accept only one unique refined region overlapping an initial candidate.
  Multiple or unmatched refined results remain unresolved.
- After successful refinement, that camera/selection revision prefers the
  precise description, retaining the upright/horizontal query as fallback
  when absent. Editing selection invalidates this preference through revision
  keys. This is semantic refinement, NOT persistent physical-instance identity.
- Input, default-empty display, single winning view, old YOLO paths, model
  weights, official proposal generation, and production boundaries are retained.
- UI now says candidates are non-unique and uses `当前暂无可采用的目标框`
  instead of implying an already-submitted target was never selected.

## Verification and limits

30 focused tests pass in the actual VLX environment (existing SQLAlchemy
deprecation warning only). Regression cases cover the actual overlapping bee,
the actual distinct guitars, rejection of a huge region absorbing a tiny
object, specific-query selection, and multiple/unmatched verification rejection.

On a later saved scene, a precise query selected the desired displayed guitar
and excluded the other candidate; upright and held test images also passed.
The precise query alone missed the old horizontal crop, so orientation fallback
was retained. The orientation-rich precise query still selected two guitars
in the later scene; it was not adopted as a universal solution.

The later saved scene differs from the report: the bee is now upright, rather
than lying down. Diagnostic files are under external runtime
`toy-guitar-vlx-dual/reported_misses_20261023/`; manual evaluation rectangles
were inspected only AFTER inference and were never supplied to the models.
The corrected historical bee region can initialize an eight-point CPU track
on that later source, but this is not historical full-frame motion validation.

Actual 30-second-after-load dual-target check:
`toy-guitar-vlx-dual/miss_fix_live_check_20261023/`.

- Camera 1: six recognition frames, no selected target (current items were
  outside its view); mean interval 5.391 seconds, final interval 4.313 seconds.
- Camera 3: bee in six/six outputs; guitar accepted in two/six outputs.
  Four initial precise verifications still returned multiple candidates;
  later verification resolved one, and precise-query preference retained it.
  Mean interval 5.078 seconds, final interval 4.016 seconds.
- Visual inspection of the final composite confirms both boxes in camera 3
  on the later scene. This does NOT establish stable guitar discrimination,
  recognition of the original lying pose, universal placement accuracy, or
  the requested average three-second interval. Those remain open.

The visible dual preview was restarted with empty input after this check.
Users can re-enter the selected test target(s). No false certainty or
calibrated confidence claim is attached to semantic refinement/view scores.
