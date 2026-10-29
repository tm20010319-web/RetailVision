# VLX separate large/small guitar annotations

Date: 2026-10-23
Status: LIVE / CURRENT PLACEMENT VERIFIED / GENERAL PLACEMENT OPEN

## Confirmed request

User clarified that the two white instruments differ in fixed appearance,
not only physical size, and supplied a clear reference with both on the bar.
The left/small instrument has a rounded body, round sound hole and dark neck;
the right/large instrument has a double-cutaway body and electric pickups.
User accepted: 吉他 or 电吉他 selects both, 大吉他 or 小吉他 selects only
that instrument. Empty input still means no annotations. Each target retains
its own single winning camera. The bee remains supported as a third target.

## Changes and scope

- Standalone VLX preview target IDs are now 0=bee, 1=大吉他,
  2=小吉他. These are local preview classes, not production catalog keys.
  Generic Chinese guitar aliases, including the former 白色电吉他 alias,
  expand to (1, 2); explicit names select one class. UI help reflects this.
- Queries use `white electric guitar` and `white ukulele` respectively.
  The display keeps the user's 大吉他/小吉他 names. No box-area, location,
  fixed camera coordinates, calibration or comparison of pixel size assigns
  the instrument identity. The supplied photo informed semantic descriptions;
  it is not an image-embedding reference input or a fine-tuning dataset.
- Output parser accepts distinct instrument category names. Broad old
  `white guitar` output cannot silently become the large guitar. If one
  essentially identical region is labeled as both instruments (IoU >= 0.7),
  both conflicting guitar candidates are suppressed rather than double drawn.
- Per-category duplicate merging, independent optical-flow tracks, revision
  invalidation, empty selection, quality ranking and one-view drawing remain.
  Real window controls cover large, small, group and clear selections.
- Removed the previous all-white guitar ambiguity-verification/cache path
  and broad horizontal fallback: those paths treated both as one class and
  could choose the wrong instrument. Both single-view and dual-view VLX
  entries use the distinct categories; dual-view uses one model pass per frame.
- Original custom YOLO scripts/weights are retained. No vendor files, weights,
  camera settings, production, identity, Offline V1 or Provider A changes.

## Actual verification

33 focused tests pass in the actual VLX environment, with the existing
SQLAlchemy deprecation warning. Tests cover the new group/single selection,
clear/revision cancellation, category identity despite reversed pixel areas,
conflicting labels, independent per-guitar winners and actual two-panel
rendering, as well as existing bee duplicate, geometry and tracking checks.

External evidence folder:
`D:/project/RetailVisionDev/runtime/toy-guitar-vlx-dual/guitar_variants_20261023/`.
Original reference is preserved as `reference.png`; raw model outputs and
rendered predictions are retained. Automatic official WeDetect proposals only;
no manual evaluation rectangles are supplied to the models.

- Both compact categories and longer shape descriptions found the correct
  two regions on the user's reference and saved full camera-1 image. Compact
  queries were faster and selected for runtime.
- Production recognition replay (`validation_results.json`) passed all six
  specified reference/camera-1 cases: both guitars, large only, small only.
  Adding the bee category kept both correct guitar outputs on this saved
  scene (no bee visible in that camera). A prior held-guitar frame returned
  the bee and large guitar. Saved camera-3 image returned neither guitar.
- The old blurry horizontal crop still returned no large guitar. Removing
  the old broad fallback does NOT establish equal recall on that historical
  crop; horizontal/occluded/low-detail cases remain open.
- Thirty seconds of actual dual-camera recognition after model loading,
  selecting 吉他 (`live_check/`): camera 1 returned both correctly in 9/9
  recognition frames; camera 3 returned neither in 9/9. These are results
  for the current placement, not an accuracy estimate or a camera-3 negative
  ground-truth qualification. Mean confirmation intervals were 3.2735s and
  3.2851s; mean inference times 1.6961s and 1.6232s. Final display FPS was
  about 16.8/16.4. Final composite was visually inspected: cyan large guitar
  and pink small guitar on camera 1 only, with distinct names.

The two-camera visible preview is restarted with empty input. Arbitrary
placement/movement, rear views, lookalike instruments, tiny distant details
and real same-object winner switching are not qualified. View quality remains
a heuristic, never a calibrated recognition probability. No training or
universal physical-instance identification is claimed.
