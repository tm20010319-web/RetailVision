# VLX two-recording recovery

Date: 2026-10-28
Status: ACTIVE / NOT ACCURACY ACCEPTED

The user requested fixing both uploaded recordings, then generating new videos:

- `2026_10_28_01_51_25.mp4`: six clear operations by two anonymous users;
  existing result has two pickups, no confirmed putdowns.
- `2026_10_28_01_39_57.mp4`: at least nine clear operations by three operators;
  existing result has four pickups (including a false dog record), two putdowns.

User also confirmed displaying BOTH per-object totals across all users and
per-user/object counts. Distinct users each handling one item once should each
show one, while the item total increases. Toss/catch remains one handling session.
No frontend/database work or personal identification is in scope.

## Diagnostic evidence

Replaying all 884 saved observation rows from 01:51:25 twice reproduced two
pickups/no putdowns, with all decisions identical. Six-pair acceptance fails.
Raw/source evidence shows warm-light color vetoes rejecting the real pink toy,
orange/bee label confusion, moving reacquisition suppression, pixel-track loss,
and strict hand/contact evidence losing later pickups.

Working artifacts and pre-change source snapshots:
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261028_015125_v7_meta`.

`probe_replay.py` performs exact sequential source decoding with recorded model
and pose inputs. The early `baseline_probe`, `continuity_probe`, `no_veto_probe`
used frame seeking on VFR HEVC and are INVALID for action comparisons. Subsequent
`*_aligned` and whole-recording probes start at frame one and decode sequentially.
Do not use OpenCV frame-index seeking for exact source correspondence here.

## Current edits (provisional)

- `scripts/vlx_interaction_vision.py`: maintain an existing pixel-track identity
  across a fresh conflicting region label; permit moving, pixel-corroborated
  model corrections; contextualize the pink color veto using established local
  instance history. Provenance diagnostics record overrides/preservations.
- `scripts/vlx_interaction_tracking.py`: retain expired pixels briefly for NEW
  proposal association without emitting expired observations; require stronger
  overlap before keeping an older CSRT box over a fresh localization.
- `scripts/run_vlx_interaction_video.py`: item totals above per-user counts.
- `test/test_vlx_interaction_recovery.py`: regressions for temporal label swaps,
  moving correction and warm-light continuation, plus rejecting unseen yellow
  objects as pink. Combined interaction/video tests currently 62 pass.

These edits are not yet accepted as a full fix. The latest exact saved-model
replay (`appearance_memory_probe`) recovers the first pink pickup, yielding
three pickups/one putdown; two sessions remain incomplete, later operations are
still missed, and the first pink release is late. No final video delivered.

## Subsequent refinement (current, 2026-10-28)

The provisional CSRT/semantic-reconciliation changes were withdrawn after the
source probes did not sufficiently improve recall. Exact copies remain under
`v7_meta/abandoned_csrt_changes`; the original `vlx_interaction_vision.py` and
`vlx_interaction_tracking.py` were restored from this task's `before` snapshot.

The current optional backend is `scripts/run_vlx_interaction_refined.py`, with
configuration `config/vlx_interaction_refined.yaml` and stage implementation
`scripts/vlx_mask_refinement.py`. VLX supplies fixed instance labels; SAM3.1
supplies fresh mask tracking. Recorded VLX/pose reuse is explicitly disclosed.
No manual event insertion, target counts, or per-recording action times enter
the runtime decisions. Frame preparation always decodes the source sequentially.

SAM point-only propagation did not retain these objects, and CPU tracker-state
storage failed during reconditioning; neither experiment is used. Text-based
mask tracking with VLX-box identity association works. Bounded 150-sample windows
avoid GPU memory growth. The accepted window strategy searches up to 50 already
covered overlap samples for an unambiguous restart; a missing target at a single
mid-throw boundary must not be dropped for the entire next window. Archival full
resolution masks move to CPU, while tracker state keeps its supported device.

Shared interaction/user changes:
- A currently held object's observed support origin survives the 30s idle TTL.
- Resting extent jitter and partial reacquisition do not repeatedly reset all
  baseline/release evidence. An interior fragment can rearm a known origin,
  but cannot itself establish a new origin or confirm release.
- A transient second wrist is not an irreversible handover. Sustained exclusive
  other-user contact is still treated as uncertain ownership.
- A continuous observed fast lift/throw/catch by the same user can complete
  pickup confirmation after changing hands; missing tracking cannot prove this.
- Mutual confident person matches are eliminated before resolving remaining
  candidates. Distinct new bodies can obtain their own anonymous IDs, and
  clothing/geometry receive equal weight to prevent swaps during crossings.
- Overlay includes item totals and per-user/item details on the same panel.

Current evidence (not yet a final delivery):
- `test_20261028_015125_v8`: full fresh mask refinement, 6 pickups/6 putdowns,
  each of 3 toys handled once by each of 2 users. Full video encoded and fully
  decoded successfully (H.264, 1760x922, 5 fps, 184.8s). A display-only rerender
  moved divider lines below the text. Per-session timings remain visual estimates.
- `test_20261028_013957_v8`: superseded diagnostic because a mid-throw window
  initialization missed the bee. Do not deliver or accept this run.
- `test_20261028_013957_v9`: toy group completed all 1209 samples, 3 bee and
  3 dog sessions complete. Guitar group is still running under supervisor
  `v7_meta/run_refinement_batch.py`; inspect `refinement_running.json` and logs.
- Current targeted test suite: 66 passed. Fixtures include real guitar return
  jitter, partial body extents, and crossing users. Existing negative cases for
  ambiguous identity, switching objects, stationary holds, and toss counting pass.

Remaining before delivery: finish guitar tracking, inspect all three guitar
sessions, render the long recording, run final decode and layout checks on both
videos, update this worklog/index, and verify live preview restoration. The live
preview supervisor saves [1,2] and restores in `finally`; restoration must be
checked after all GPU work, not assumed from the process launch alone.

## Final two-recording results (2026-10-28)

The preceding remaining-work paragraph is superseded by this completion record.
Both exports now contain per-object totals and per-user/object counts. The source
recordings and previous delivered runs remain unchanged.

- `test_20261028_013957_v9`: 9 pickups, 9 putdowns, no incomplete sessions.
  Bee, yellow robot dog and white electric guitar each total 3, with anonymous
  users 1, 2 and 6 each taking each item once. IDs are video-local person tracks,
  not identities or a claim that six people operated the items.
- `test_20261028_015125_v8`: 6 pickups, 6 putdowns, no incomplete sessions.
  Orange, white egg and pink plush each total 2; users 1 and 2 each take each once.
- Both directories contain `annotated.mp4`, `actions_review.mp4`,
  `final_counts.jpg`, `result.json`, event/observation logs, and
  `delivery_validation.json`. Full videos are H.264, 1760x922, 5 fps;
  durations 245.6s and 184.8s respectively. Full and excerpt videos decoded with
  ffmpeg `-xerror` without errors. Duration drift is below one output frame.

The guitar propagation experiment was not accepted: it followed the empty stand
and prematurely ended the second hold. `scripts/vlx_grounded_instance.py` now
validates the fixed VLX instance against independent SAM3.1 semantic masks.
Neighbouring guitars remain competing instances. VLX geometry is retained when
the mask agrees, including visible partial masks; an overlapping pixel track may
bridge at most one second after semantic confirmation. The original VLX jump to
the neighbouring blue guitar at 117.361s is rejected. Strong nearby semantic
recovery after a short gap is explicitly discontinuous, never invented motion.

The final production `ground` stage ran freshly for all 1209 source samples.
Its detector uses all required checkpoint weights (missing weights: none). The
generic image adapter's unused fourth FPN level was removed; 3.1 has three levels.
The earlier generic adapter discarded that fourth level before grounding, and
its diagnostic evidence is retained under `diagnostic_grounding_4`. The final
manifest excludes the superseded `masks_4` propagation group.

Rules now distinguish the configurable 4s person-occlusion grace in the refined
runner from the unchanged 2s missing-object grace. A pending pickup retains its
original hand through bounded uncertainty, without accumulating confirmation
time during missing/ambiguous evidence. After unique same-hand recovery, further
co-motion can confirm even if a bystander overlaps. Returning to the origin
cancels an unconfirmed onset. Existing no-switch, no-new-hand, no-fabricated-release
and toss/catch negative regressions remain passing. Policy is
`instance_semantic_continuity_v8`.

Guitar intervals are 51.440-58.640s, 74.321-90.521s and 116.761-126.961s
(7.2s, 16.2s and 10.2s). These are visual estimates, not frame-perfect physical
grasp/release truth. In particular the last pickup confirms only at 121.361s
because of occlusion; the causal overlay shows pending before confirmation and
then uses the original observed onset. Some release confirmations lag the visible
placement. This two-recording review is not a general cross-camera qualification.

Validation: 72 targeted tests pass (`test_vlx_interaction_video.py`,
`test_vlx_interaction_recovery.py`, `test_vlx_interaction_tracking.py`); edited
Python modules compile. The recorded guitar regression exercises the actual
candidate selector and interaction rules, all three sessions, plus rejection of
the blue-guitar label switch. The original deterministic replay failed with only
two guitar sessions before the pending/semantic recovery changes.

Both results reuse saved VLX and pose evidence, and freshly run SAM3.1 tracking /
semantic validation. Anonymous association and events were recomputed. They are
not fresh VLX inference. Final screenshots verify the item totals, all participant
rows, and the corrected guitar box/countdown at 77.12s and 122.16s. Review excerpts
cover 29-131s and 104-153s. User visual acceptance is still pending delivery.

Live preview restoration: the explicit `vlx-seek-py311/Scripts/python.exe`
launcher is required. `psutil.cmdline()` reports the shared base `agents-py311`
executable and must not be replayed literally, because that bypasses the venv
and fails to load `accelerate`. A retry exposed this, and the explicit launcher
restored the correct model process with selection [1,2]. Final runtime is
`toy-guitar-vlx-dual/20261028_052145_294934` (PID 26980 at verification).
Camera 3 is fresh with increasing source frames (1303 to 3370). Camera 1's pinned
identity HTTP endpoint repeatedly closes without a response (`RemoteDisconnected`),
including a direct authenticated read; HTTP/RTSP TCP ports are reachable. Its
existing retry loop remains active. Do not claim both cameras are restored, and
do not bypass the identity pin. This is disclosed separately from video delivery.

## Fresh model probe and live preview

`run_gpu_probe.py` supervises `recognition_probe.py`, comparing original text
with object shape descriptions at exact source frames of BOTH recordings.
It saves the current preview PID/selection, stops gracefully, and restores the
original preview in `finally`. Check `gpu_probe_exit.json`, preview restoration,
and `fresh_query_probe/predictions.jsonl` before continuing.

The preview originally selected [1,2] (large/small guitar), PID30140. The GPU is
16GB and does not fit both inference workers; do not run competing GPU models.
Preserve existing videos/results; use distinct output directories and accurate
fresh-versus-recorded provenance for all subsequent tests.
