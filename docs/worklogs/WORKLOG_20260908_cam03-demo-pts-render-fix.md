# cam03 demo original-PTS rendering fix

Status: `VIDEO FIX VERIFIED / SOURCE AND OLD OUTPUT PRESERVED`

## Scope and cause

The user reported cyan person boxes detached from people near output PTS 74.0
and 96.5 seconds. The affected file was
`reports/cam03_20260908_demo_video/cam03_member_behavior_miaoxiang_demo.mp4`.

The prior renderer was only present in the separate worktree at
`.claude/worktrees/cam03-publication-qualification/scripts/render_cam03_member_demo.py`.
It read an FFmpeg rawvideo pipe using original-frame indices, but default
FFmpeg synchronization expanded the 4745 original frames to 4859 decoder
frames. Original PTS indices therefore selected earlier images. Approximate
drift at the reported moments was 3.84 and 3.98 seconds. Total MP4 duration
alone did not reveal this error.

Placing the unchanged same-Run observations onto correct original source
frames recovered alignment in both reported cases. No new detection or
tracking inference was necessary.

## Implementation

The reusable renderer is now `scripts/render_cam03_member_demo.py` in the main
checkout. It preserves the existing presentation implementation and adds:

- `-fps_mode passthrough` for source decoding, maintaining one decoder frame
  per original PTS entry. CFR 25 fps presentation is performed explicitly
  afterwards, selecting the greatest source PTS not later than output PTS.
  Repeated display frames do not create analysis observations or behavior facts.
- Early-EOF, extra-frame and subprocess-exit checks; per-output-frame mapping
  evidence; source/Run checks and source/input SHA-256 records.
- Person/product observation lookup against the displayed source frame's PTS.
  Existing 0.35-second nearest-observation matching remains bounded; no track
  is synthesized across a long gap.
- Member labels use the formal same-Run association's actual interval with
  no extra padding. Track 1's saved interval is PTS 13.6–194.0 seconds; the
  other worktree's statement that it ends at 152.4 seconds is inaccurate.
- Chinese product names taken from this Run's raw behavior facts, which have
  names absent from the product-track export; status card moved below source OSD.
- Original audio copied, separate output directory, refusal to overwrite a
  non-empty output directory, and final MP4 naming only after successful encode.

The source is still `D:/GJJ/work/video/2026_09_07_14_47_22.mp4`, SHA-256
`b7133e373c3d072252bbd2c771bcd0b6e4d9a2fdb8d7638c6982148c24500490`.
Run remains `cam03-a824bd52328e4e5c8354c1fe6708eb32`.

## Validation and output

`test/test_cam03_member_demo_render.py` uses real FFmpeg with four distinct
lossless frames and a PTS gap. Before the fix, the production decode command
returned 12 frames and failed the count assertion. After the fix, both exact
frame count and pixel sequence pass: **1 test passed**.

Output directory: `reports/cam03_20260908_demo_video_pts_fixed/`.

- `cam03_member_behavior_miaoxiang_demo_pts_fixed.mp4`: H.264, 1280×720,
  25 fps, 4859 display frames, 194.36 seconds, 19,741,803 bytes. Original AAC
  audio retained (190.848 seconds). Full FFmpeg decoding passed.
- All 4745 original PTS entries independently matched ffprobe; all 4859
  output-to-source mappings passed; source and input hashes remained unchanged.
- Actual encoded MP4 body-interior comparison against original frames:
  PTS 74.0 RGB mean absolute error decreased from 60.28 to 2.88; PTS 96.48
  decreased from 77.38 to 3.10. These are image alignment checks, not detection
  accuracy scores. The reported people are inside the expected boxes.
- Nine keyframe previews saved. Visual checks covered the two drift cases,
  pickup, intermediate holding, both putdowns and the end of the recording.
  Timers show 0.8 seconds at PTS 34.0, 11.8 at PTS 45.0, 2.8 at PTS 140.0,
  and final holdings of 21.4 and 6.4 seconds after putdown.
- `render_report.json`, `frame_mapping.jsonl`, `verification.json` and
  `verify_render.py` preserve provenance and reproducible verification.

Output SHA-256:
`2e28f20b048373da8e1fac9eb3553e88e06ab973d7e1bc0c25d7be9f4a5b0988`.

## Commands

Run from `D:/GJJ/work/code`; select a new output directory for every rerender:

```powershell
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe scripts/render_cam03_member_demo.py --output-dir reports/cam03_member_demo_rerender
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe -m unittest test.test_cam03_member_demo_render -v
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe reports/cam03_20260908_demo_video_pts_fixed/verify_render.py
```

## Stage boundaries

1. **Identity:** existing confirmed Track 1 → member-gaojunjie retained;
   formal 0.75 policy and accepted similarity 0.75773224 unchanged.
2. **Manual import/query:** earlier read-only review verified six imported
   events in the main `demo_flow.db` and both actual member MCP tools. This
   video-fix session performs zero database writes and no Agent run. The prior
   Agent-answer artifact was not located, and import-script idempotency remains
   unfinished; this work does not close those items.
3. **Video:** reported rendering drift fixed and verified, with old source,
   overlay, worktree scripts and output preserved. This is a render-only fix;
   no SAM3 rerun, new download, recognition threshold or publication gate change.

Camera-burned OSD and the declared absolute-time origin are retained as input
evidence; their pre-existing discrepancy is separate from PTS alignment and
has not been silently corrected. Controlled download and automatic publication
remain pending later validation. The other worktree's blanket completion
claims are not adopted by this record.
