# Seven-minute home-item inference and first-group entry review

Date: 2026-10-28
Status: FILE DELIVERY COMPLETE / ENTRY COMPARISON VERIFIED / GOODS ACCURACY FAILED

User selected `D:/surveillance_video_raw/9f61f8000a5603edfe3f21295de00760.mp4`
and confirmed the two recently annotated targets are home monitor and AI eye
massager. They also requested reusing the prior YOLO entrance-region business
logic to count the first arriving group, stopping when the group has entered.
They require native results, raw intermediate records and relative-time facts;
no upload API or database work is in scope.

## Delivered evidence

Directory: `runtime/video-analysis/20261028_9f61f800/`.

- Source SHA256: `42effb5b35bfb8f9b329cc20b36f5b93e3b62f8b501a8c2fff96fb9dbcfffb76`.
- Source image 960x544, 22 fps, video PTS duration 420.0 seconds; container/audio
  tail extends to 420.096 seconds. Source-relative times are not inferred UTC.
- Fresh full-video VLX/WeDetect, pose and CSRT analysis produced 1849 samples,
  zero pickup/putdown sessions. The small targets are not reliably localized;
  e.g. a 180s eye-massager candidate lands on a person's head. Candidate samples
  are not validated detections or accuracy metrics. Zero events must not be
  interpreted as no real interactions.
- SAM3.1 refinement was not launched because a verified initial mapping of
  both requested instances was unavailable. No arbitrary label/box was promoted
  into a trusted initialization; no trained home-item YOLO weights were used.
- Top-level requested files: annotated.mp4, result.json, events.jsonl,
  manifest.json, interaction_facts.json and interaction_facts.jsonl. Empty fact
  lists/JSONL are intentional and faithfully represent the automatic output.
  Raw VLX/pose/tracking evidence is in interaction/observations.jsonl.

## First entering group

Replayed the first 20 seconds using BehaviorVisionAdapter (YOLO11n person,
staff_vest, YOLO11n-pose on CPU) and unchanged BehaviorMonitor state logic.
The original normalized `behavior_cameras.entrance-1.json` entry polygon is
shown in the output. Its calibration remains provisional; the original
2560x1440 calibration versus 960x544 video mismatch is disclosed. No physical
distance or verified-calibration claim is made.

Source review shows five distinct first-group entrants. Original customer-only
logic emits four events. Track 14, the blue/green-top person, is classified as
staff by the vest model and excluded. Replaying identical saved detections with
all persons eligible (comparison only) yields five geometric crossings at
5.409091, 5.590909, 7.409091, 11.181818 and 11.590909 seconds. This matches the
visible first group. Staff/customer identity is not independently verified;
five is the all-person entry-region crossing count, not confirmed memberships.
Initially inside persons are not counted. No hand-entered events or target
counts enter either replay.

entry/ preserves original output and roles; entry_review_clear/ preserves the
explicit role-filter comparison. Top-level entry_annotated.mp4, entry_result.json
and entry_events.jsonl expose the comparison separately from merchandise facts.
No production rule, geometry, threshold, live model or service was changed.

## Bounded export repair and verification

Existing `export_vlx_acceptance.py` required `recorded_run` even for fresh
inference and recognized only the prior six toy/guitar keys. Added the two
confirmed home-item labels and allowed missing recorded_run only for explicit
fresh_inference. Reused evidence still fails without a source reference.
Four additional regression cases cover both labels, empty fresh facts without
invented events, and rejecting source-less reused evidence. Source reports are
unchanged. Behavior monitor and acceptance-export tests: **83 passed**.

Both delivery videos fully decoded with ffmpeg -xerror. Source/code hashes,
video hashes and monotonic timestamp checks are in validation.json. Export is
file-only, production_import_ready=false, with store/camera/verified recording
start metadata still unknown. README.md documents limitations and file paths.
