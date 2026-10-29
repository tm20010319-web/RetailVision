# VLX occlusion and release repair

Date: 2026-10-28
Status: IMPLEMENTED / REPLAY VIDEO VERIFIED / USER REVIEW PENDING
Role: historical continuity/validation; not production or architecture authority.

The user explicitly requested changing the current project after the 17:56:11
test exposed white false splits, orange delayed release and a missed pink lift.
No frontend, database, camera or delivery archive changes were performed.

## Implementation

`agent/vlx_interaction.py`, policy `occlusion_aware_release_v9`:

- Missing wrist permits release only with visible hand and body clearance.
- Shrunken fragment centred on owner cannot independently prove release.
  A rotated smaller object away from the owner can still release.
- Partial owner-occluded target may use the configured 15-second retention
  lease, with explicit `duration_includes_occlusion` and `occlusion_intervals`.
  Ordinary loss remains two seconds. Timeout, owner loss and disjoint recovery
  stay incomplete. No unseen detections or manual events are fabricated.
- Expiring known-rest contact origins plus same-hand/object co-motion resolve
  supported ownership under bystander proximity; new hands cannot borrow origins.
- Fragments can maintain a started known-support release, but both endpoints
  require full resting extents and the intermediate lease is bounded.

Video overlay and acceptance export retain the occlusion estimate. Export nested
fields are validated/allowlisted. Refinement records effective settings and
selected targets. Official scripts expose tracking window size (default still
150) and `render --reuse-refinement-from` into a new directory.

## Validation

Three real evidence regressions failed before fixing, then passed. Broad pickup
margin widening was withdrawn because it regressed old cases. An overly broad
partial-object release veto was narrowed after adding a failing old pink-video
regression. No diagnostic variants remain in application code.

Final tests: 108 passed, one existing SQLAlchemy warning. Test files:
`test_vlx_refinement_replay.py`, `test_vlx_occluded_interactions.py`,
`test_vlx_interaction_video.py`, `test_vlx_interaction_recovery.py`,
`test_vlx_interaction_tracking.py`, `test_vlx_refinement_initialization.py`,
`test_vlx_acceptance_export.py`.

Complete older-evidence replays retain 9/9 and 6/6, same user/object pairs;
some putdown times change by 0.2–0.6 seconds. Diagnostic helpers and reports
are under `runtime/vlx-interaction-video/test_20261028_175611_fix_meta`.

## Result

`runtime/vlx-interaction-video/test_20261028_175611_v5`:

| User | Object | Source interval seconds | Holding seconds |
|---|---|---|---|
| 2 | pink first | 62.724–64.324 | 1.600 |
| 3 | white egg | 65.724–137.161 | 71.437 |
| 2 | pink second | 67.125–148.163 | 81.038 |
| 1 | orange | 67.725–101.128 | 33.403 |

White includes inferred occlusion 122.729–133.729 (11 seconds), disclosed in video
and JSON. Timing is a 2D source-time estimate, not frame-perfect support truth or
cross-camera qualification. Action order/count and label review pass; user
acceptance remains pending.

V5 reuses completed v3 VLX/pose/SAM evidence and recomputes behavior/video only.
Manifest mode: `recorded_vlx_pose_sam31_behavior_replay`; no fresh inference is
claimed. New single-window SAM retry v4 hit sustained near-limit GPU memory
pressure and was stopped by verified task PID. Its status is aborted, not a
completed run. General arbitrary-occlusion window handoff remains unresolved.

H.264 1760×922, 5 fps, 185.2s, 926 fully decoded frames; SHA256:
`58e286559298b296a1ad9df1ca1a671698e756bfca7d7c80a3de41170177a865`.
Output `修复说明.md` lists source files, replay commands, locations and limitations.
No source files deleted/renamed. Existing ZIP/bundle not rebuilt, so earlier
archive recipients do not yet have these fixes. Other worklog entries preserved.
