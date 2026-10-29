# Entrance-camera replay and separated merchandise delivery

Date: 2026-10-28
Status: VIDEO VERIFIED / USER-CONFIRMED VIDEO-ONLY ROLE CORRECTION COUNTS FIVE

## Subsequent user-confirmed role correction

The user explicitly requested correcting only the blue-top woman in this
video to customer, preserving trained models. Added an isolated override bound
to source SHA256 and exact token `store-1:entrance-1:223703-entry:5`, then
replayed the same observations and unchanged entry rules/geometry.
Delivery: `runtime/video-analysis/20261028_223703/doorway_customer_corrected/`.
Customer count is now five; the additional entry occurs at 24.921s. The other
four events, timestamps, all boxes and non-target observations are unchanged.
Raw model roles and user override provenance are preserved alongside effective
roles. This is human-corrected role evidence, not improved model inference.
The original four-count video/result remain unchanged in doorway_review/.
All three model weights, behavior modules and production camera configuration
have identical before/after SHA256 hashes. Full video decode and artifact
comparison passed. No model training, production change or database writes.

## Original run retained below

The user corrected the camera view: entry belongs to
`D:/surveillance_video_raw/2026_10_28_22_37_03.mp4`. The seven-minute
`9f61f8000a5603edfe3f21295de00760.mp4` is now merchandise-only. The earlier
entry overlay on that seven-minute view is superseded, not calibrated evidence.

Reused existing BehaviorVisionAdapter (YOLO11n/ByteTrack, staff_vest, YOLO pose)
and BehaviorMonitor unchanged. Fresh CUDA inference covered all 2267 source
frames, 91.041 seconds, 2560x1440. Initial isolated doorway strip extended too
far into the store: track 22 turned left and became occluded before satisfying
the inner-edge crossing; customer/all-person counts were 3/4. Evidence retained
at `runtime/video-analysis/20261028_223703/`.

Replayed identical saved detections with the confirmation edge immediately
inside the glass doorway. Final pixel polygon, outside-left clockwise:
[(480,855),(1660,810),(1680,875),(465,920)]. This is provisional local geometry,
not verified metric calibration. No production camera config, homography,
model weight, tracking threshold, staff filter or entry state rule changed.

Final delivery: `runtime/video-analysis/20261028_223703/doorway_review/`.
Original customer filter emits four events at 19.601, 19.921, 20.841 and
25.201 source-relative seconds. All-person comparison adds blue-top track 5
at 24.921s (model role staff), giving five distinct first-group crossings.
The group clears the doorway by 28s; no further entry events in the remaining
recording. Five is a geometric/visual count, not verified customer identities.
Manual review stays in visual_review.json, separate from automatic events.

Full 1920x1080/15fps annotated video, result.json, events.jsonl,
all_person_events.jsonl, manifest.json, per-frame observations.jsonl and review
evidence retained. ffmpeg full decode passed; output 91.066667s differs from
source by less than one output frame. All timestamps increase, event keys are
unique, original production rule hashes unchanged. No new algorithm tests
needed for this artifact-only run; no production code was edited this turn.

Seven-minute goods artifacts were copied into
`runtime/video-analysis/20261028_9f61f800_goods_only/`, including the requested
video/result/events/manifest/facts and raw observations. This explicitly reuses
the already completed 1849-sample full-video inference; no new inference is
claimed. Target-instance localization failed, so reliable pickup/putdown counts
and durations remain unknown. Raw zeros and empty facts are preserved, with
delivery_status.json marking accuracy failure. SAM was not run without trusted
initialization. No DB writes, HTTP service work or cross-camera identity joins.
