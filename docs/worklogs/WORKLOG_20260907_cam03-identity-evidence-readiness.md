# cam03 Identity Evidence Readiness

Status: `MAPPING WIRING TESTED / OLD SAMPLE ANONYMOUS / REAL VALIDATION BLOCKED`

## Frozen Sample

- Source: `D:/GJJ/work/video/2026_09_06_19_16_00.mp4`.
- SHA-256: `48c1cdbb5d4e5da2c77add261859f5304b8517a302abf3b23f23422fd976051d`.
- Replay: `reports/cam03_20260906_behavior_validation/replay/full_replay.json`.
- Replay SHA-256: `a77708bd2b67019da5c60a859d0520a69627ab6e673f468a52c27be1afb155d0`.
- Six original Stage9 events unchanged, all attached to Person Track 2.
- Original demux PTS: `time_base=1/90000`, `duration_ts=10188090`, duration 113.201 seconds.
- Reused previous video-overlay interpretation, as requested: local start
  `2026-09-06T19:11:00+08:00`, Asia/Shanghai. This is not the filename time.
  No additional independent clock calibration was performed this turn.
- UTC-naive window: `[2026-09-06T11:11:00,2026-09-06T11:12:53.201)`.
  All six relative event times are inside this window.
- The old replay used OpenCV FPS-based ticks. The new capture entry supplies
  original ffprobe PTS to the existing sampler; this does not rewrite old PTS.
- The old 12/11-event recordings and 0.75/0.65 experiments were not modified.

## Mapping Repair

The member API factory previously constructed the current-projection SQL
repository without the existing historical revision repository. It now wires
that repository into `save_mapping`. Once a device identity exists, both the
current mapping and revision are committed by the existing atomic mechanism.
Initial pending registrations without a device identity remain pending only.
Revision time uses UTC now, never the mutable `last_sync_at` field.
Replacing a member's device identity also closes its old open revision.

The new bootstrap command queried cam03 face groups and member metadata using
the existing client. It does not upload photos or write device settings.
Diagnosis found that QueryPersonInfo omits UniqueID and supplies FaceUUID;
the checker now reuses the existing synchronization fallback to FaceUUID.
Names are not matching keys. Regression tests reproduce the actual response.

All three existing enabled, synced mappings matched the current device's group
UUID, FaceUUID, PersonID and successful model state. Historical revisions were
started at the actual verification time, not backdated:

| Member | Effective From (UTC-naive) |
| --- | --- |
| member-wudongxin | 2026-09-07T02:59:25.859196 |
| member-gaojunjie | 2026-09-07T02:59:25.864707 |
| member-lushun | 2026-09-07T02:59:25.868708 |

These are current device-member mappings, NOT assignments to old Person Tracks.
No reliable immutable cam03 mapping revisions covering September 6 were found.
Old projection sync dates were not accepted as historical proof.
Only identity projection/revision writes were performed. No BehaviorEvent was
inserted, updated or published; the old window still has zero formal rows.

## Same-Run Evidence

The saved old replay has no `person_tracks`. Its product-association diagnostics
contain Person tokens but no Person bbox. They cannot reconstruct body snapshots.
No new snapshots were grafted onto the old six events; no SAM inference was run.

`scripts/run_cam03_evidence_capture.py` now provides an artifact-only entry over
the explicit retained b45ed08 two-guitar analyzer worktree. This is not a migration
of historical algorithms into the main branch. New runs use independent UUIDs,
their own Stage9 events, original demux PTS, Person/Product observations, actual
source/analysis dimensions and resize transform, and a hash manifest.
Source-bound initialization confirmation/frame checks remain required.

The main and isolated existing analyzer exporters now include full Person tokens,
runtime ID, bbox, observed time, source sequence/PTS, dimensions, clipping and
consecutive observed sample counts. This measured decoder resize is explicitly
NOT a verified FaceReco-to-recording calibration.

Isolated edited file (must retain with this local workflow):
`D:/GJJ/work/retailvision_cam03_probe_20260906_b45/scripts/run_offline_complete_loop_golden.py`.
Its diff is preserved in the readiness report directory for traceability.

## Real Evidence Result

- Real cam03 member-metadata requests executed; current mappings verified.
- Read-only historical Recognition query executed in existing unqualified
  diagnostic Provider mode. Coverage is complete, one record exists.
- Similarity 0.66492233 is rejected under unchanged 0.75 Acceptance Policy.
  No new threshold experiment was performed.
- Historical resolver still returns unmapped for the old Recognition timestamp.
- Both old Person Tracks are `unavailable`; Track 1 has zero raw events,
  Track 2 has six. Confirmed member behavior count: **0**.
- Formal Provider did NOT execute; spatial association/aggregation was not run
  without prerequisites. An empty accepted diagnostic query is not proof of
  formally qualified unmatched identity.
- Persisted cam03 Push fingerprint exists: model T5AI-W/IPG-5950PCS-AI,
  firmware 3.4.0-20260430-EN. Serial presence is audited without printing it.
- Formal cam03 profile resolves `profile_not_found`; cam02 verification was not
  copied. Face 1920x1088 to recording 2560x1440 geometry lacks fixed-scene proof.
- The DB has zero cam03 device-store, camera-store and device-camera registrations.
  No historical scope registration was invented or backdated.
- Port 8081 had no listener at the final preparation check. No receiver was
  silently restarted and no realtime environment was globally changed.

## Verification and Delivery

161 tests passed plus 22 subtests across new mapping/export tests, existing SQL
repository, identity, media, behavior, pipeline and member API tests. The new
entry was exercised with a simulated analyzer; CLI help/import passed in the
SAM environment. No new full GPU inference or real member association pass is
claimed. Initial pytest collection required `--import-mode=importlib` and an
explicit test PYTHONPATH because the repository name conflicts with Python code.

Artifacts: `reports/cam03_20260907_identity_readiness/`.
Start/recording steps: `docs/cam03_new_sample_recording.md`.

Next real validation requires a newly recorded sample after current mappings
are effective, continuous cam03 Push receipt, current scope registrations,
verified same-device clock/geometry/profile evidence, and same-Run body snapshots.
Old video remains useful as an anonymous behavior sample, not as a recoverable
formal member-binding sample. This phase is not production validated, published,
fully closed, or a completed real member-association validation.
