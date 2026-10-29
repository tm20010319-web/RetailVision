# Toy/guitar independent YOLO training

Date: 2026-10-21
Status: `SINGLE-VIDEO BASELINE TRAINED / RELOCATION NOT VERIFIED`

## User-confirmed scope

Create new configuration and training entry files, retaining the original
staff-vest workflow, and follow its data-check/train/evaluate/artifact process.
Use the current every-three-seconds annotation package for the specified toy
and white guitar. Relocation/independent-video qualification is later work.

## Independent implementation

- `config/toy_guitar_train.yaml`
- `tools/training/train_toy_guitar.py`
- `docs/TOY_GUITAR_TRAINING.md`
- `test/test_toy_guitar_training.py`

No production runtime or business persistence changes. The existing
staff-vest scripts/configuration/checkpoint are retained and fingerprinted.

## Data and planned run

Source annotation package:
`D:\RetailVisionDev\runtime\annotations\2026_10_21_16_56_05_3s`.

202 source images, 326 boxes: toy 202, white_guitar 124. Original source
images/labels are copied with SHA-256 checks into
`data/toy_guitar_dataset_20261021`.

Chronological blocks within each class-presence state produce:

| Split | Images | Toy | Guitar | Guitar absent |
| --- | ---: | ---: | ---: | ---: |
| train | 142 | 142 | 87 | 55 |
| val | 31 | 31 | 19 | 12 |
| test | 29 | 29 | 18 | 11 |

No exact duplicate images were found. 9143 perceptually similar cross-split
pairs are explicitly recorded; all images are from the SAME mostly static
recording. This is an initial same-video baseline, not independent-video or
relocation qualification. Neither status may be claimed from these metrics.

Training: local `data/models/yolo11n.pt`, detect, 640, batch 16, epochs 80,
patience 20, seed 42, GPU 0, separate Ultralytics settings and output.

Run output: `runs/toy_guitar/baseline_v1_20261021`.

## Verification so far

- Five focused unit/integration tests pass, covering both classes, invalid
  coordinates/classes, source preservation, chronological visibility-aware
  split membership, disclosure of shared-source similarity, tamper detection
  and refusal to overwrite an experiment.
- Real 202-image preparation/validation dry run passes.
- CUDA is available on RTX 5070 Ti in the restored agents-py311 environment.

## Executed training and evaluation

GPU training completed successfully in 57.28 seconds with 38 epochs, stopping
under the configured patience=20 policy. The best result was at epoch 18.
`best.pt`, `last.pt`, curves, confusion matrix and saved configuration/log exist.

The same-video held-out test contains 29 images and 47 boxes:

| Class | Precision | Recall | mAP50 | mAP50-95 |
| --- | ---: | ---: | ---: | ---: |
| toy | 0.9983 | 1.0000 | 0.9950 | 0.9950 |
| white_guitar | 0.9966 | 1.0000 | 0.9950 | 0.9950 |

At confidence 0.25, predictions have the expected class counts on all 29
test images: 29 toys and 18 guitars. All 11 guitar-absent images have no
guitar prediction. Actual model predictions, rather than annotation overlays,
are under `test_predictions/`; all 29 also appear in
`test_predictions_overview.jpg`.

These scores are for the same mostly static video only. 9143 near-duplicate
cross-split pairs remain disclosed. No independently recorded video or new
placement was tested. Relocation qualification remains `NOT_VERIFIED`.

All five focused tests passed after the settings-path adjustment. The initial
Ultralytics import used a new project-root settings folder because its desired
parent did not yet exist. The entry now creates the dedicated parent first;
after the process completed, only that newly generated settings folder was
moved under `runs/toy_guitar/_ultralytics_settings/Ultralytics`.

SHA-256 checks confirm the original staff-vest configuration, training script,
validator and model checkpoint are unchanged. No production model selection,
camera configuration, SAM3 contract, service or business database was modified.
