# Toy/guitar precision correction V4

Date: 2026-10-22 (Asia/Shanghai)
Status: `WHITE-CLOTHING CORRECTION LIVE / REMAINING BEE AND OCCLUSION ERRORS`

## Confirmed goal and scope

The user wants the actual bee plush and white electric guitar detected while
white garments and a different yellow object on the white exhibit stand are
excluded. At the start, the bee was on the green chair and the guitar lay on the
table, partly behind the lamp. These objects are positives, not confusing
background. The user selected live confidence 0.50; it remains fixed for this
correction. This is the separate YOLO preview, not VLX, tracking, instance
identity, Offline V1 development, or a production qualification.

The previous threshold adjustment hid low-score outputs but did not restore
true-target recall. The newly pinned development regression reproduces that
failure at 0.50 on seven current-pose holdouts: 0 TP / 0 FP / 14 FN.

## Reviewed data and lineage

- Preserve all 443 previous dataset images, labels and splits, including the
  original 323 V2 entries. New output:
  `data/toy_guitar_dataset_v4_precision_20261022`.
- Capture 40 verified warm-decoder live frames at one-second intervals in
  `D:/project/RetailVisionDev/runtime/annotations/2026_10_22_precision_live`.
  Review all 40; minimum within-box template correlation 0.98635.
  Select train 1-18, validation 25-28, test 35-40; intervening frames are buffers.
- Tight bee visible extents `[332,107,360,142]` include antennae. Guitar box
  `[390,151,507,216]` bounds the visible body and distal neck tip; the lamp
  occludes the intervening neck. A rectangular box necessarily also includes
  the occluder. The annotation does not pretend those covered pixels are
  visible guitar.
- Review 113 supplementary snapshots/crops in
  `2026_10_22_precision_reviewed`. Exclude one old snapshot with AI labels
  already drawn into its pixels, and nine byte-identical derived crops.
  Select 103: 84 train / 7 validation / 12 test. Training derivatives from the
  frozen dataset come exclusively from its training split. New live derivatives
  inherit their parent temporal split. No test example is reassigned to train.
- The added selection totals 131 images. Combined data: 574 images, 914 boxes;
  train 407, validation 77, test 90. There are 74 selected background-only
  images, which include white/light clothing, chairs and the user-identified
  yellow exhibit object. In full positive frames these distractors remain
  unlabelled background while both true items are labelled.

All 113 derived images were visually reviewed in six contact sheets before
training. Manifests retain source hashes, crop coordinates, physical times
where available, derived-sample indexing scope and split lineage. Live-source
digests describe capture records and image hashes, not a nonexistent video
file. Static same-scene blocks and derived views are correlated and do not
establish independent-video or arbitrary-location accuracy.

## White-garment recording investigation

The user recalls the event at approximately 17-18h. The matching camera's
recording index supplied 19 files between 17:01:04 and 17:58:55. Files and
digests are saved in runtime `precision_recording_review/`. The exact reported
event was not identified by the user; representative white-clothed passages
were found and reviewed. The magenta-corrupt clip is excluded from training.

Crop training uses clip 01 and clip 17 passages. Clip 12 is validation; clip
14 is test. Five full-camera test frames and three validation frames also
check real input scale, because enlarged garment crops alone are insufficient
for live-input verification. Full-frame and cropped views of the same passage
are correlated and must not be counted as independent people or events.

Baseline `precision_baseline/precision_regression.json`:

- All seven current-pose item holdouts fail at 0.50 (14 FN).
- Eleven background-only test crops have no detections at 0.50.
- Eight full-camera white-clothed frames have no garment-region false item
  boxes at 0.50 and 2 such boxes at 0.25. Both genuine items at the older
  chair placement are correctly detected in those frames at 0.50.

This means the new model must restore current-pose recall while preserving
the 0.50 exclusion behavior; a reduction in 0.50 garment false positives
cannot be claimed from a baseline already at zero.

## Training and planned checks

Configuration: `config/toy_guitar_train_v4_precision.yaml`.
Reuses `tools/training/train_toy_guitar_v3.py`; no production/runtime inference
source is modified. Start from the selected chair checkpoint; 100 epochs,
AdamW lr0 0.0002, mosaic 0.25, existing camera/inference geometry. Comparison
baseline is the chair checkpoint at the same confidence 0.50 and IoU 0.50.
Existing V3 reports/replays at 0.25 retain that historical threshold.

Dataset preparation/deduplication/hash checks pass. Original V2/staff artifact
protection remains enabled in the training entry. The baseline regression was
run and failed before training. Candidate evaluation, visual review, current
live verification and model selection are pending actual results.

## First candidate result and context correction

The first 100-epoch run completed in 255.287 seconds. Its best checkpoint
SHA256 is `f4ccd5ca6a2779e96ce8ddf60ea01a295f5f5fc26dd03713ca5efa137156afaf`.
At confidence 0.50 and match IoU 0.50, all 14 current-pose target instances
recover (0 FP / 0 FN), and all eleven background-only test crops remain clear.
Frozen V2 results are unchanged: 86 TP / 1 FP / 1 FN; the known annotation
audit adds one FN. All other existing relocation holdouts retain their counts.
The eight protected V2/staff artifacts pass hash comparison.

However, the full-camera white-garment check exposes a guitar false positive
at 17:43:36 (clip 14, requested +50 seconds), confidence 0.56136. All 16 true
instances remain detected, but the candidate fails the garment regression and
is **not deployed**. The last-epoch checkpoint also has one garment false
positive; it is not a passing alternative. Review sheets show the remaining
box on the white-clothed person. Crop-only exclusion did not transfer reliably
to full-camera input scale. This is the evidence motivating the next run,
not proof that context alone is the model's internal cause.

The context correction preserves all 574 previous entries and adds 27
full-camera images: 24 training from the already designated clip 01/17
passages, and 3 validation from clip 12. All 27 annotated full frames were
visually reviewed. Real bee/guitar objects at the old chair placement remain
positive, and garments are background. Clip 14 remains test; no failed test
frame is reassigned to training. New source:
`D:/project/RetailVisionDev/runtime/annotations/2026_10_22_precision_context`.

Configuration: `config/toy_guitar_train_v4_precision_context.yaml`.
Output: `data/toy_guitar_dataset_v4_precision_context_20261022`.
Run: `runs/toy_guitar/precision_v4_context_20261022`.
Start from the first candidate, 100 epochs, lr0 0.0002, mosaic disabled.
Confidence remains 0.50. The earlier test failures have now informed model
development; these checks are developmental regressions, not an untouched
independent accuracy qualification.

## Context candidate results and actual live miss

The context run completed 100 epochs in 256.622 seconds. Best checkpoint
SHA256: `ea3aad8453ffaa458334195633066e4c3ce113023dd8c2f3f12e92f70f5b59d5`.
At confidence 0.50 and matching IoU 0.50:

- Seven initial current-pose holdouts: 14 TP / 0 FP / 0 FN.
- Eleven negative crops: no detections.
- Eight full-camera garment frames: 16 TP / 0 FP / 0 FN; no garment-region
  false boxes at either 0.50 or the diagnostic 0.25 threshold.
- Denser garment checks: 29 test frames (58 TP) and 19 validation frames
  (38 TP), all without FP or FN. Adjacent frames are correlated.
- Frozen/other relocation counts remain unchanged; all eight protected
  artifact hashes match.

The context checkpoint was loaded by live PID 51976 at 21:09:50, retaining
confidence 0.50. The first 30-second observation
`precision_context_live_20261022_2110/summary.json` has 263 fresh valid states,
8.78 FPS, no camera faults, bee present in all states and guitar absent in all
states. Visual inspection shows the user moved the guitar upright onto the
green chair and the bee onto a white chair. This is a real live miss, not a
passing result. Both were detected after the guitar was moved horizontally
again, but that does not resolve the upright failure.

## Upright correction and failure reproduction

Original camera recording:
`precision_placement_review/AI_20261022210928-20261022211129.mp4`,
SHA256 `cd3a46a0b17d8ba80b8beec0ae008f9e3cab82cf71ae451dfa2d0a2ef10e123e`.
Use original pixels rather than annotated live screenshots. Review all 34
stable frames from requested +77 through +110 seconds. At 800x448, manually
reviewed visible boxes are bee `[317,157,350,186]` and upright guitar
`[350,46,389,140]`; minimum within-box template correlation is 0.982615.

Preserve all 601 context entries and original V2 splits. Add 19 images:
11 train (+77..86 and +88), 3 validation (+96..98), 5 test (+106..110).
Frame +87 is excluded as a byte-identical training duplicate; intervening
frames remain buffers. No failed test frame is reassigned to train.
Result: 620 images / 1006 boxes, train 442 / validation 83 / test 95.
Source annotations and exclusions:
`D:/project/RetailVisionDev/runtime/annotations/2026_10_22_precision_upright`.

Before training, `precision_upright_baseline/precision_regression.json`
reproduces 5 TP / 0 FP / 5 FN on the five upright test frames: the bee is
detected, the guitar is missed. Initial current-pose targets and clothing /
yellow-distractor exclusions still pass. Configuration:
`config/toy_guitar_train_v4_upright.yaml`; output dataset:
`data/toy_guitar_dataset_v4_upright_20261022`. Train the third candidate from
context best.pt for 100 epochs, lr0 0.0002, mosaic disabled, confidence 0.50.
Run: `runs/toy_guitar/precision_v4_upright_20261022`.
Final regression and actual live outcome are recorded below.

## Final upright candidate and deployment

The third run completed 100 epochs in 257.234 seconds. Best checkpoint:
`runs/toy_guitar/precision_v4_upright_20261022/weights/best.pt`, SHA256
`233d383cb48ab33e510e897deb30afd8741657a9f75b7f2e2d096e81479ea91b`.
At confidence 0.50 and matching IoU 0.50:

- Five upright test frames improve from 5 TP / 0 FP / 5 FN to
  10 TP / 0 FP / 0 FN. All seven initial current-pose holdouts retain
  14 TP / 0 FP / 0 FN.
- Eleven negative crops (five clothing, four yellow distractor, two white
  chairs) have no model boxes.
- Eight full-camera garment challenge frames retain 16 TP / 0 FP / 0 FN;
  garment-region false boxes are zero at 0.50 and diagnostic 0.25.
- Dense garment test (29 frames / 58 instances) and validation
  (19 frames / 38 instances) retain zero FP and FN at 0.50.
- All other relocation groups retain their previous counts. Frozen V2
  retains 86 TP / 1 FP / 1 FN; its known annotation audit yields 2 FN.
  This historical error was not hidden by the new checks.
- All eight protected artifacts retain their hashes. Five regular review
  sheets and four dense garment sheets were visually inspected.

Only the weights path in `config/toy_guitar_live_v3.yaml` changes from the
context candidate to the upright candidate. Camera/inference parameters,
confidence 0.50, original datasets and protected business/staff files remain
unchanged. No production inference source is modified for this correction.

Gracefully stop old PID 51976 and start PID 77876 at 21:26:15. Fresh runtime
status confirms the selected weight SHA256, and the responding preview window
is `Behavior-1 | Yellow Bee & White Guitar | Q to close`.
Actual live interval 21:26:44-21:27:14: 261 fresh valid states, both classes
exactly once in all 261 states, mean 8.68 FPS, maximum recorded input frame age
0.125 seconds, no camera-input faults. All six sampled annotated images were
visually inspected: true bee upright on a nearer white chair and true guitar
propped upright on the blue table. No other model boxes appear in these
snapshots. This is the user's current placement, different from the green-chair
upright training pose. It remains a short static observation, not motion or
arbitrary-placement qualification.

The current table-upright guitar box slightly truncates the top of the
headstock. Body and most of neck are included; pixel-tight extent is not
established. No person is present in this live interval, so clothing exclusion
is supported by the recorded regressions, not by the live counts. User's exact
17-18h clothing incident remains unconfirmed.

## Saved evidence and continuation

Final run contains `precision_review/precision_regression.json`,
`precision_dense_review/summary.json`, `live_validation/summary.json`,
`live_validation/visual_review.json`, six live images, raw observation samples,
and `model_selection.json`. The first candidate summary records its rejection;
the context summary and its saved `live_validation/` and
`upright_failure_regression/` retain the real live miss. Final training summary
references actual selection and live evidence rather than `pending`.

Checks are correlated same-camera development regressions; earlier failures
informed model selection. The current display is YOLO detection, not VLX,
identity tracking, or a claim of guaranteed recognition while moving. Future
misses should be investigated using original recordings and reviewed labels;
do not silently lower the user's 0.50 threshold or use AI overlays as training
pixels. Existing Offline V1 / Provider A boundaries remain unchanged.

## Subsequent live movement reopens correction

At approximately 21:29:36, the user carries the guitar while wearing a dark
shirt with a light chest graphic. The fresh live output misses the real guitar
and marks the chest graphic as the bee. This is a new observed failure after
the passing static interval, which remains valid only for its recorded scene.
Do not describe the current model as passing a movement test or completion
of the full accuracy goal. Continue using original recording
`precision_motion_review/AI_20261022212737-20261022212938.mp4` and reviewed
hand-held / garment-background labels. Camera-native face/edge OSD is present
in the recording; the separate preview's item predictions are not training
pixels. Earlier static evidence and model selection remain historical.

## Hand-held correction: reviewed data and pre-training failure

All six annotation sheets (31 images) were visually reviewed before training.
The new source `2026_10_22_precision_handheld` contains 26 full-camera frames
with both genuine items and five background-only garment-graphic crops.
Visible guitar extents include light-garment hand-held, dark graphic-shirt
hand-held, and horizontal table placements. Severe ambiguous occlusions at
requested +18,22,26,30,92,94,98,102 seconds are excluded, not negative labels.
The real bee is now on the white exhibit stand at `[241,90,264,122]`.

Preserve all 620 prior images/splits and add 18 train, 6 validation and 7 test
images; crops inherit their parent's split. Dataset preparation reports
651 images / 1058 boxes, split 460 / 89 / 102. All original 323 V2 entries,
labels and split assignments remain intact. Manifests retain original video
hashes, image hashes, requested times, crop geometry and split lineage.
These are short, correlated development blocks from the same camera.

Before training, the upright checkpoint reproduces the new failure at 0.50:
light hand-held test 1 TP / 1 FP / 3 FN; dark hand-held 2 TP / 0 FP / 2 FN;
horizontal table 4 TP / 2 FP / 0 FN. Combined 6 frames: 7 TP / 3 FP / 5 FN.
The 12 background crops and earlier current/upright positive groups still
pass. Thus crop-only exclusion again does not establish full-scene success.
The baseline and subsequent live-failure annotation are retained under the
upright run's `handheld_failure_regression/` and training/model-selection
summaries without changing its earlier static metrics.

Configuration `config/toy_guitar_train_v4_handheld.yaml` starts from upright
best.pt, 100 epochs, AdamW lr0 0.0002, mosaic disabled. Confidence remains
0.50. Dataset `data/toy_guitar_dataset_v4_handheld_20261022`; run
`runs/toy_guitar/precision_v4_handheld_20261022`. Final regression, dense
garment checks, visual review and deployment are pending actual results.

## Fourth candidate: improvement with remaining localization/miss

The fourth run completes 100 epochs in 263.984 seconds. Best SHA256 is
`aa56660ca5797fe491fdd37a6461a941417cd531dd266e7afa1b5d991ce8ed1d`.
All protected artifacts and earlier frozen/relocation results remain intact.
The six new full-camera test frames improve to 10 TP / 1 FP / 2 FN:
light hand-held 3 TP / 1 FP / 1 FN, dark hand-held 3 TP / 0 FP / 1 FN,
horizontal table 4 TP / 0 FP / 0 FN. Visual review shows a truncated guitar
neck in the raised upright light-shirt pose and a missed partly occluded
guitar at requested +120 seconds. The light-pose FP is a localization mismatch
on the genuine guitar, not a garment false box. Last.pt has the same counts.
This candidate is not yet selected for the live preview.

The 12 background test crops, 8 original full-camera garment frames and dense
48-frame garment challenge remain clear, with all true items detected in the
garment passages. Four unused interleaved seconds +113,+115,+117,+119 were
extracted from original pixels. Before/after visual inspection shows chest
graphic false bees removed and the hand-held guitar recovered at exact +119;
the real bee is occluded there. These adjacent seconds are correlated, not
an untouched independent qualification.

A one-variable inference-size probe at 640,800,960,1280 does not solve all
remaining new-pose misses. Keep live geometry unchanged. To improve scale
coverage while preserving test boundaries, derive 30 context/whole-guitar
crops exclusively from the existing 15 positive training frames. All five
sheets are visually inspected; no true item is partially cropped, and labels
are coordinate transforms of the reviewed full extents. Exclude one
byte-identical static guitar crop. New selected source contains 29 training
images / 44 boxes, no new validation/test images. Combined dataset:
680 images / 1102 boxes; train 489, validation 89, test 102. Dataset checks pass.

Scale-correction config `config/toy_guitar_train_v4_handheld_scale.yaml` starts
from the fourth candidate, retains 100 epochs/lr0 0.0002/mosaic disabled and
0.50. New run `runs/toy_guitar/precision_v4_handheld_scale_20261022`.
Final selection remains pending measured regression and live evidence.

## Fifth candidate: selected inference and actual limits

The scale run completes 100 epochs in 288.718 seconds. Best checkpoint SHA256:
`b91041522bae3433577199b4a3cfefdbc73b835db509dd3ede904db94852fcb9`.
Training stays at 640. Selected main inference is 960, with a guitar-only 640
retry when the current frame has no guitar detection. Both passes retain 0.50;
no old boxes, location filters, tracking or colour exclusion are used.
`scripts/run_toy_guitar_live.py:predict_item_boxes` is the shared runtime seam.
Fallback is optional and disabled for configurations that omit it.

The selected dual configuration passes the original 102 test entries with
169 TP / 0 FP / 0 FN against frozen labels. Frozen V2 has 87 TP / 0 FP / 0 FN;
the known missing-label audit still contributes one FN. The 12 negative crops,
8 original full-camera garment frames, 48 dense garment frames, and 4 pinned
graphic seconds pass their bounded development checks. Evidence is under
`precision_review_dual`, `precision_dense_review_dual`, and
`full_selection_check_dual.json` in the scale run. These correlated development
checks do not establish independent accuracy. The raised guitar still has a
partly truncated upper neck/headstock despite passing IoU 0.50.

Single-size 960 actually fails the 22:07:42-22:08:12 live interval: guitar in
152/250 fresh states, bee in 250/250. Preserve runtime
`precision_handheld_scale_live_20261022_2207`. A 60-frame original PNG capture
at the same lamp-occluded placement reproduces 36/60 single-size guitar
detections. The actual runtime seam improves to 60/60 with the guitar retry;
before/after evidence is `precision_occluded_lossless_20261022`. Earlier JPEG
samples had hidden this near-threshold instability and are not contrary proof.

Full 1,214-frame motion replay with the selected runtime seam still produces
7 chest-graphic bee false-positive frames (at 104.9052, 106.7264, 115.9137,
116.0351, 116.1161, 119.5158, 119.6372 seconds). Preserve
`motion_replay_dual/items_motion_annotated.mp4`. A two-size bee-agreement probe
retains two of these errors, so it was not implemented as a fix. Severe
occlusion misses remain unqualified. Do not mark the accuracy goal complete.

Live PID 47640 starts at 22:21:28. The actual 22:26:19-22:26:49 observation
contains 244 fresh valid states, both targets once in all states, mean 8.09 FPS,
maximum input age 0.14 seconds, and no camera-input faults. All six snapshots
were visually inspected: real bee on the green chair, real guitar partly
occluded by the lamp. No person is present. Runtime evidence is
`precision_dual_live_20261022_2226`; this is a static scene check only.

## Latest white-clothing incident: original-pixel reproduction

User screenshot timestamp is 22:20:42; its displayed 0.57 box is at the true
table guitar. The white-clothing event is in original recording
`AI_20261022221911-20261022222111.mp4`, retrieved under runtime
`precision_new_white_report_2220_wide`. A narrow 22:18:30-22:21 query returned
no files. Widening to 22:10-22:30 returned the surrounding completed files;
do not interpret the first empty index as absent footage. No camera setting
was changed.

Sequential original decoding, resize to the actual 800x448 live input, and the
actual runtime seam reproduce 50 false-guitar frames among 424 sampled frames
over source PTS 38-72 seconds. This is subsampled footage, not 50 distinct
incidents or a general error rate. At OSD 22:19:59, source frame 1214, the false
body/arm/lower-body box scores 0.7590457, while the true guitar scores 0.5372805.
Peak sampled false score is 0.8077397. Raising confidence enough to remove
this error can therefore remove true guitars first. Errors occur with primary
960 inference and are not created by the later guitar fallback.

The visible false region combines person/garment/arm/lower body rather than
isolating the white torso. All-white colour alone is not an established causal
explanation. Matched-pixel 640/960/1280 probes retain errors; torso-only crops
are clear but also alter context/scale, so do not claim a proven single visual
cue. Current evidence supports inadequate full-scene clothing discrimination.
Random seeking did not preserve identical decoded pixels; the probe was
corrected to sequential decoding and exact PNG equality before comparison.

The small deterministic regression is runtime
`precision_white_clothing_regression_20261022.py`. It invokes the real runtime
helper on lossless original frames 1206,1214,1280,1690. Initial run exits 1:
all four have one true table guitar plus one false body-region guitar at 0.50.
All source/image/model hashes and predictions are saved in
`precision_new_white_repro_20261022/pinned_regression.json`.
The existing runtime unit suite still passes 8 tests; this does not override
the failing visual regression.

## Sixth candidate: reviewed white-clothing context correction

Review all four annotation sheets before training. Add 21 images: 15 training
views from five original full frames and two complete-context derivatives
per training frame, 2 validation frames, 4 pinned test frames. The true bee
on the floor/chair and the actual lamp-occluded guitar are labelled; human
and clothing remain background. No test frame is used for training. Original
PNG hashes, video hash, sequential source-frame ordinals, PTS and crop lineage
are retained under `2026_10_22_precision_white_context`. JPEG quality-100
dataset derivatives retain the lossless originals for exact regression.

All prior 680 dataset entries/splits remain preserved. New combined dataset
has 701 images / 1,139 boxes: train 504, validation 91, test 106. These adjacent
temporal views remain correlated development checks. Config:
`config/toy_guitar_train_v4_white_context.yaml`; run:
`runs/toy_guitar/precision_v4_white_context_20261022`. Start from scale best.pt,
100 epochs, original training settings and operating confidence 0.50.
Final results and the selected runtime policy follow below.

## White-clothing correction selected and live

The sixth run completes 100 epochs in 293.531 seconds. Best SHA256:
`ef785b82b116dbcd4d0716554c400ae69f2518c0f406f1aeff8fe38dff2bc06c`.
Standalone 960 clears the four pinned clothing errors and the 424-frame dense
new passage, but adds two false boxes on the original 102 tests: a background
guitar and a duplicate guitar. Its full replay has eight false-bee frames
versus seven in the primary model. It is rejected as a standalone replacement.
Best/last input-size and three checkpoint-interpolation probes also retain
regressions; none are deployed.

The selected runtime retains the scale checkpoint as primary and uses the
sixth checkpoint only to verify guitar candidates. Each primary guitar must
match one secondary guitar at IoU >=0.40 on the same current frame and input
size. One-to-one matching prevents duplicate acceptance. Missing accepted
guitars retry at 640 using both checkpoints. Confidence remains 0.50 for both.
Bee outputs remain from the primary. Verification is optional; configurations
without it retain single-model behavior. Both model class mappings and both
weight hashes are verified/recorded at startup. No past boxes, person masks,
colour rules, location filters, VLX or tracking are involved.

Selected eval config is
`config/toy_guitar_eval_v4_white_context_verified_base.yaml`; live config is
`config/toy_guitar_live_v3.yaml`. Actual runtime results:

- Four pinned original PNG frames: all body false guitars removed, one true
  table guitar retained in each. The same regression previously exited 1.
- New passage PTS 38-72 seconds, 424 sampled original frames: false-guitar frames
  50 -> 0, exactly one true table guitar in all 424 frames.
- Original 102 test predictions are structurally identical to the reviewed
  scale outputs: 169 TP / 0 FP / 0 FN. All eight protected hashes still match.
- Four new tests: baseline 7 TP / 4 FP / 1 FN -> selected 7 TP / 0 FP / 1 FN.
  The existing bee miss in the bent-over floor pose remains; do not claim the
  deployed policy detects all 177 new-plus-old labelled objects.
- 60 lossless lamp-occluded static frames retain both items in all 60.
- 48 earlier dense garment frames retain 96 TP / 0 FP / 0 FN. The eight earlier
  full-camera checks and 12 background crops also pass. Runtime unit suite: 8 pass.

Selected 1,214-frame replay is in the sixth run's
`motion_replay_verified_base/`. All four sheets and the two changed regular
sheets were visually reviewed. The same seven bee false frames remain; severe
occlusion still causes guitar gaps and upper-neck extent is not pixel-tight.
The frozen missing-label audit still contributes one FN. This is a bounded
clothing correction; overall accuracy remains open and tests are correlated
same-camera development evidence.

Gracefully stop PID 47640 and start PID 70528 at 22:56:12. Status confirms both
checkpoint hashes and guitar-only verification; the AI window responds.
Actual 22:56:35-22:57:05 observation: 245 fresh valid states, both true items once
in all 245, mean 8.11 FPS, max input age 0.141s, no camera-input faults. All six
snapshots visually reviewed: bee on green chair and guitar behind lamp. No
person is present; clothing exclusion is supported by original-video replay.

The sixth run's `model_selection.json`, `training_summary.json`,
`selected_live_config.yaml`, `live_validation_verified_base/`, and
`selected_visual_review_verified_base.json` preserve selected roles, failed
alternatives and residual errors. The prior scale summary links to this
correction without rewriting original failures. `white_clothing_before_after.jpg`
compares the exact 22:19:59 frame. Offline V1 / Provider A and staff artifacts
remain unchanged.
