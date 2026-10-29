# Worklog: DPI-01B Offline Video Preflight

Date: 2026-09-04
Status: `VIDEO PRECHECK PASS / REAL SAM3 ADAPTER BLOCKED`

## Input And Derived Artifact

- Original: `D:\GJJ\work\video\2026_09_04_16_43_24.mp4`
- Original stream: HEVC, `2560×1440`, 3398 video frames, approximately 139.281
  seconds, source time base `1/90000`.
- Derived test copy:
  `D:\GJJ\work\video\dpi01b_2026_09_04_16_43_24_1280x720.mp4`
- Derived stream: H.264, `1280×720`, 3398 video frames, approximately 139.280
  seconds, time base `1/12800`, SHA-256
  `18b9b727a8a8276cb07a6f5ad6c91e6b8af3976ade52bd4cd1f1352252eb5ad6`.

The original was preserved. The derived copy is outside the source tree and is
the only candidate for the frozen Stage7C `1280×720` coordinate contract.

## Preflight Evidence

- `ffprobe` decoded all 3398 video frames.
- First PTS: `0`; last PTS: `1782272` ticks.
- PTS deltas were positive for every adjacent frame; minimum delta `512`,
  maximum delta `13312` ticks.
- Three visual samples at approximately 0, 60, and 120 seconds decoded
  correctly. The display area is visible, with people occluding it in some
  periods; an operator must choose an actually unobstructed initialization
  frame.
- Focused DPI-01A suite after the video preparation: `167/167 passed`.

## Current Blocker

The verified external Stage7C modules currently expose text and bounding-box
prompt calls. They do not yet provide a verified Adapter implementation for the
DPI-01A contract of one stateful Session plus bbox, positive points, negative
points, and unique `obj_id`. No real Mask proposal or Track was created from
this video, and no `REAL_SAM3_PASS` is claimed.

## Next Authorized Step

Implement and test the real Adapter against the actual SAM3 API, then run the
interactive Offline initializer on this derived MP4. The operator must choose
the frame and 3-5 approved product keys, confirm every Mask, and review the
four required artifacts. Camera `192.0.2.3` still has no RTSP connection or
production qualification in this worklog.
