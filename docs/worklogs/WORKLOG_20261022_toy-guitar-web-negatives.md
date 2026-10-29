# Toy/guitar web negative-sample collection

Date: 2026-10-22
Status: `COLLECTION_COMPLETED / NOT_TRAINED / LIVE_UNCHANGED`
Role: development continuity and resource-preparation evidence; not a model-accuracy qualification.

## Authorized scope

User reports suspected overfitting and scarce negative examples, confirms the same camera and asks for online negative material. After a single delivery-form clarification, user selects direct download and screening into an independent project folder. Current task covers material preparation, not another training run or live deployment.

## Delivered resources

Root: `data/toy_guitar_web_negatives_20261022/`.

- 249 licensed metadata candidates from twelve Wikimedia Commons categories; 229 decoded successfully, 20 download failures excluded.
- All 229 candidate photographs reviewed in eight numbered contact sheets. Four uncertain images additionally inspected at full delivered resolution.
- 61 exclusions recorded: illustrations, actual guitars, ambiguous toy/costume content, correlated series and low relevance.
- Selected 168 images: 78 white clothing, 61 yellow objects, 29 white objects.
- One empty YOLO label per image, with no new person/background class. Files remain unsplit training candidates.
- Original author/license metadata and source URLs retained; mixed CC BY/CC BY-SA/CC0/public-domain licenses apply individually. Derivative resizing/JPEG conversion recorded.
- Actual delivered bytes pass image-decode, SHA256, image/label-count and empty-label checks; zero exact duplicate hashes.
- `README.md`, `manifest.csv`, `manifest.json`, `ATTRIBUTIONS.md`, `summary.json`, representative overview and six selected contact sheets support review and reuse. Rejected candidate files are isolated in `review/`.

## Boundaries and recommended next work

No training, live restart, threshold change or existing data/split modification performed. The existing scale-primary/white-context guitar-verifier policy and previous precision worklog remain the live context. Offline V1, Provider A, business/staff artifacts and camera settings are outside this task.

Internet images expand distractor diversity but do not replace camera-specific real negatives. In a camera frame containing the real guitar or bee toy, those targets must remain positively annotated. Empty labels are only appropriate when targets are absent. Suggested training starting range is 15–25% negative images, subject to precision/recall validation, with no claimed optimal ratio.

Keep video-session/source lineage together across train/validation/test. Adjacent same-camera frames and reviewed development clips are not an independent final test. Future acceptance must include a fresh held-out camera recording with white clothing/yellow distractors and target movement, measured at the unchanged 0.50 threshold. Dataset preparation alone establishes no model improvement.

The broader overfitting/accuracy goal remains open; see `WORKLOG_20261022_toy-guitar-precision-v4.md` for residual motion bee false positives, occlusion misses and localization limits.
