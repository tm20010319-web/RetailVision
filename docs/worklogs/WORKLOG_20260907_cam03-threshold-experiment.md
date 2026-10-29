# cam03 Recognition threshold experiment

Status: `EXPERIMENT COMPLETED / IDENTITY UNAVAILABLE`

At the operator's request, tested the existing Acceptance Policy and
DurablePushInboxRecognitionProvider at thresholds 0.75 and 0.65. No manual
member assignment was supplied and no persisted threshold/status was changed.
The SQLite connection enforced `mode=ro` and `PRAGMA query_only=ON`.

Input remains the September 6 source with SHA-256 `48c1cdbb...`, six raw
Stage9 events, and the half-open UTC window 11:11:00 to 11:12:53.201.
Source stream `duration_ts=10188090` times `time_base=1/90000` yields
113.201 seconds. The previous video-overlay start interpretation was retained.

Results:

- Coverage: complete, one Recognition record, similarity 0.66492233.
- At 0.75: rejected by `similarity_below_threshold`.
- At 0.65: similarity accepted, then Provider failed closed with
  `historical_mapping_unavailable` / `mapping_historical_unmapped`.
- cam03 immutable historical revision count: zero. Current mapping projections
  and operator statements were not substituted for historical revisions.
- Accepted identity evidence: zero; same-camera spatial association and Track
  identity aggregation could not run. This is not an end-to-end association pass.
- The saved replay also lacks exported same-run Person snapshots; no snapshots
  or geometry qualifications were fabricated.
- Confirmed member behavior count: zero; business DB writes: zero.

The Provider was used in its existing **unqualified diagnostic mode**, without
a capability profile. This isolates the threshold question; it neither qualifies
cam03 nor bypasses production publication gates. The historical resolver's lower
bound was the earliest persisted revision, recorded explicitly as a diagnostic
bound, not asserted as a cam03 history bootstrap.

Added artifact-only script: `scripts/probe_cam03_recognition_threshold.py`.
Output: `reports/cam03_20260907_threshold_experiment/threshold_experiment.json`
and `artifact_sha256.json`. Existing mock and replay artifacts were not altered.
