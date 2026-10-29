# VLX continuous camera preview

Date: 2026-10-23
Status: LIVE PREVIEW RUNNING / THREE SCREENSHOTS REPLAYED / PLACEMENT ACCURACY OPEN

## Authorized scope

User wants the existing bee plush and white electric guitar recognized and
boxed throughout the camera view, with a delay of several seconds acceptable.
User explicitly keeps the prior YOLO implementation and accepts VLX-Seek's
official WeDetect proposal dependency (which contains YOLOWorld structure).
This is an independent experimental camera preview, outside Offline V1,
Provider A, SAM3, identity, publication and business database behavior.

## Implementation

- `scripts/run_toy_guitar_vlx_live.py`: persistent local NF4 VLX model;
  official automatic WeDetect proposals regenerated for each sampled frame.
- `config/toy_guitar_vlx_live.yaml`: camera settings reused from the previous
  preview, without loading its custom YOLO models.
- `scripts/start_toy_guitar_vlx_live.ps1`: separate launcher.
- No fixed product coordinates, prior boxes or SAM tracking seed recognition.
  Zero or one target is a valid result. Boxes are shown on their own sampled
  frame, with capture time and total inference latency; expired results clear.
- Normal query: `bee plush toy; white guitar`. If guitar is absent, a second
  query against the same proposals adds `white guitar lying horizontally on
  a table`. It supplements the normal query; it is not a location filter.
- Seek's selected regions are not assigned fabricated confidence values.
- This is text-conditioned localization of the described objects, not a
  verified instance-reidentification system. Similar-looking duplicates and
  arbitrary placement/pose/occlusion/night performance remain unqualified.

## Verification

`python -B -m pytest -q -p no:cacheprovider test/test_vlx_live_contract.py
test/test_vlx_seek_trial_contract.py`: 8 passed (one existing SQLAlchemy warning).

Actual provided-screenshot replay, final fallback policy:
`D:/project/RetailVisionDev/runtime/toy-guitar-vlx-live/screenshot_check_fallback_20261023/`.

| Screenshot | Selected target | Full cycle seconds |
| --- | --- | ---: |
| 1, hand-held bee | bee plush | 3.91 |
| 2, upright guitar | white guitar | 1.66 |
| 3, horizontal guitar crop | white guitar, supplementary query | 2.84 |

The baseline normal query missed screenshot 3. Three prompt variants were
tested with identical automatic proposals; only the horizontal description
recovered that crop. Raw results are in `prompt_check_20261023/`. This is
development evidence, not an independent accuracy estimate. The selected
horizontal region also does not establish full-neck localization accuracy.

The independent visible preview was launched for identity-verified
behavior-detection-1. `runtime_root/current.json` identifies its active run.
Initial live run `20261023_095500_132324` processed 10 successive sampled frames:
mean full-cycle latency 3.17 seconds (3.06--3.88 seconds). Eight emitted both
target labels, two emitted only bee. This is an observation of model outputs,
not an audited 80% accuracy claim. Visual inspection of `latest.jpg` confirms
the bee on a chair; the guitar is partly obscured by the pendant lamp and
its detection/localization is intermittent. No arbitrary-placement, motion,
full-neck localization or instance-identity acceptance is claimed.

That run was stopped gracefully and the final source was restarted with
explicit stale-camera clearing. The independent VLX preview stays running
for user placement testing; Q/Esc or the run-local `stop.request` exits it.
The previous YOLO code, weights, configurations and launchers remain intact.
