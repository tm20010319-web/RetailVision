# Bee placement/pose detection miss diagnosis

Date: 2026-10-21
Status: `MISS REPRODUCED / NO FIX DEPLOYED`

User confirmed the yellow box disappears after moving the bee toy. The live
camera feed is healthy and the guitar remains detected. A real, warmed-up
RTSP source frame visibly contains the bee but yields no class-0 box at the
unchanged confidence 0.25. Two pinned-frame replays reproduce this exactly.

Artifacts and red-capable replay command:
`D:\RetailVisionDev\runtime\toy-guitar-live-camera1\diagnostics\bee_moved_20261021\DIAGNOSIS.md`.

Observed comparisons:

- Original-pose reference: bee confidence 0.984699, correctly localized.
- Changed live pose, 640/0.25: bee missed on both repeats; guitar 0.989802.
- Same failed frame, confidence 0.01: bee candidates only 0.037626 and
  0.025935, including a partial-object candidate.
- Same failed frame, input 1280/0.25: bee still missed.
- A synthetic local 90-degree rotation did not restore detection. It is kept
  as a marked diagnostic, not training/real qualification evidence.

The strongest explanation is inadequate training coverage of changed toy
appearance/pose. The initial 202-image recording is almost entirely one toy
placement/pose, and its 29 held-out frames share that scene. Those high metrics
do not qualify relocation or new orientations.

The active preview calls `model.predict` and has no persistent object tracker.
Thus a missed frame immediately removes its box. No evidence points to a
renderer/class mapping fault; the raw model output already lacks the bee.

No checkpoint, live configuration, threshold, camera setting, runtime code or
business data was changed. Live preview remains running and this failure is
not declared fixed. Future work needs a separately qualified data expansion
and/or a real tracking requirement, without treating low-confidence boxes as
successful recognition.
