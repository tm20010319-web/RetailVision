# Toy/guitar multi-pose annotation and YOLO V2

Date: 2026-10-21
Status: `COMPLETED / PINNED MISS RECOVERED / LIVE MODEL UNCHANGED`

User authorized supplementing both target classes using
`D:\surveillance_video_raw\2026_10_21_22_12_09.mp4`, followed by a separate
training version. Existing 3-second sampling and original-file preservation
requirements carry forward. This work does not authorize tracking features or
deployment of the resulting model into the live preview.

## Annotation and data

- New video: 8899 decoded frames, approximately 362 seconds, 800×448.
- Exact media-time sampling: 121 JPEGs at 0, 3, …, 360 seconds.
- Source SHA-256: `32b2232f6b770a2d81fc5ffbe0556d653c6e4de73b3f86a0ed35867d3cbddb0f`.
- Annotation root: `D:\RetailVisionDev\runtime\annotations\2026_10_21_22_12_09_3s`.
- 242 boxes: 121 yellow bee plush / 121 white guitar. IDs stay `0: toy`,
  `1: white_guitar`; Chinese previews identify the bee explicitly.
- Individually inspected changing poses and occlusion frames 49–66; inspected
  all 121 annotated contact-sheet images. Static-frame template checks have
  minimum similarity 0.9967895, shifts ≤ 1 pixel.
- Frames 56/57 have a guitar truncated at the image bottom; frame 62 is heavily
  occluded and used only in training. Labels enclose visible target extent.
- No synthetic imagery, source pixel edits, person labels or black-object labels.

Independent V2 configuration/entry point:
`config/toy_guitar_train_v2.yaml`, `tools/training/train_toy_guitar_v2.py`.
Combined data: 323 images, 568 boxes; train/val/test = 224/50/49.
V1 split membership and image/label hashes are preserved. New dynamic test
frames are 52, 56 and 59; validation frames are 55 and 57. The pinned historical
miss image is excluded as an exact file from every dataset split.

The recordings share the same camera/scene, including similar static/adjacent
poses across splits. 11,932 cross-split perceptually similar pairs are disclosed.
The historical miss replay now has its pose represented by other new training
images, so it is a regression check, not an unseen-pose qualification.

## Training and verification

YOLO11n local pretrained weights; input 640; batch 16; GPU 0; max 100 epochs;
patience 25; rotation ±45°, translation 0.15; mosaic disabled for the last 15
epochs. Experiment: `runs/toy_guitar/multipose_v2_20261021`.

Nine focused V1/V2 preparation/matching tests passed (five existing subtests
also passed). Actual combined data dry-run passed all 323 image/label hash,
class, geometry, source membership and duplicate checks.

Training completed 75 epochs with early stopping, best at epoch 50. CSV training
time is 109.02 seconds. The first evaluation attempt failed because the
Ultralytics list loader returned synthetic names (`image0.jpg`); changed the
prediction wrapper to one source path at a time and added a regression test.
Recovered evaluation using the existing checkpoint and identical recorded
configuration, with no retraining or altered test membership.

Raw 49-image/87-reference-box comparison, confidence 0.25 / NMS IoU 0.70 /
matching IoU 0.50: V1 TP/FP/FN = 76/2/11, V2 = 87/4/0. New-video test subset
matches all 40 targets in 20 images; the three dynamic test images match all
six targets, with three duplicate predictions. Raw-reference mAP50 = 0.995,
mAP50-95 = 0.967667; threshold-swept AP does not imply no fixed-threshold errors.

Visual review identified a V1 label omission: old frame 78 already contains a
hand-carried white guitar. The frozen source/dataset/old results remain intact.
A separate post-evaluation audit adds visible bbox [130,133,187,183], giving
88 references. Audited V1 TP/FP/FN = 76/2/12; V2 = 87/4/1. The V2 guitar
candidate in old frame 78 is too loose (IoU about 0.46), hence still fails the
0.50 localization criterion. The other three extra boxes are duplicates,
not separate objects. This correction did not alter training, model selection
or confidence thresholds.

Pinned real RTSP miss replay: V1 misses twice and native Python exits 2; V2
detects twice at confidence 0.939837 and IoU 0.968719, exit 0. Commands/native
exit codes are in `regression_exit_codes.json`; assertions and visual comparison
are retained. This qualifies that pinned regression only, not arbitrary moves.

Final artifacts: `runs/toy_guitar/multipose_v2_20261021/weights/best.pt`,
`training_report.md`, `training_summary.json`, `v1_v2_comparison.json`,
`heldout_annotation_audit.json`, 49 Chinese prediction images and comparison
sheets. The annotation ZIP passes CRC checks, with 121 original images and
121 label files. All annotated sheets and held-out prediction sheets were viewed.

Original staff-vest hashes equal the prior recorded values. V1/live files were
not edited. The recovered process additionally checked protected hashes before
and after evaluation; its report explicitly scopes this preservation check to
evaluation recovery rather than claiming a persisted pre-training hash snapshot.
Live preview configuration/model remain unchanged; no camera/service started.
