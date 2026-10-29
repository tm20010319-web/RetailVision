# cam03 formal profile and geometry closure

Status: `GEOMETRY VERIFIED / PROFILE CONFIGURED / TRACK 1 CONFIRMED member-gaojunjie / NO FORMAL BEHAVIOR WRITES`

## Fixed input

All work is bound to the existing Run `cam03-a824bd52328e4e5c8354c1fe6708eb32`
(source `D:\GJJ\work\video\2026_09_07_14_47_22.mp4`, SHA-256
`b7133e373c3d072252bbd2c771bcd0b6e4d9a2fdb8d7638c6982148c24500490`, verified
against the run manifest before any step). Its person snapshots, product
tracks, and six Stage9 facts were reused unchanged. SAM3, Stage8, Stage9,
thresholds, and prior artifacts were not modified. No algorithm was added; the
existing binder, validator, aggregator, and projector ran unmodified.

## Geometry: vertical_padding_unverified resolved

The candidate 8-pixel padding came from `_geometry_diagnostic`'s pure
aspect-ratio arithmetic (1920/1.7778 = 1080; 1088 − 1080 = 8). It was checked
against real same-Run material and REFUTED:

- The accepted Recognition record (occurred_at 2026-09-07 06:41:22 UTC,
  similarity 0.75773224) carries a 1920×1088 cam03 BackgroundImage in the
  durable inbox. Its bottom rows 1080–1087 contain real scene content
  (per-row std 57.6, max 255) — not letterbox padding.
- A recording frame at PTS 22.0 s (run start + 22 s = the face event time;
  fixed camera, static scene) was extracted with ffmpeg from the source video.
  The existing `qualify_p6s_face_spatial_geometry.py` could not run because
  neither environment has the `av` module; frame extraction used
  ffprobe/ffmpeg on original PTS instead. The qualification method itself
  (fixed-scene correspondences, human-reviewable transform) was followed.
- SIFT fixed-scene correspondences: 1558 ratio-test matches, 1458 RANSAC
  inliers, reprojection error median 0.304 px / p95 1.169 px / max 2.969 px
  (same order as the archived cam02 validation: 276 inliers, median 0.369 px).
- The homography is a pure diagonal scale: scale_x 1.333404 (nominal
  2560/1920 = 1.333333), scale_y 1.323481 (nominal 1440/1088 = 1.323529),
  offsets sub-pixel. The face-frame corners map onto the recording-frame
  corners within 0.2 px.

Conclusion: the 1920×1088 face stream is a full-frame anisotropic resize of
the 2560×1440 recording FOV. crop=none, padding=none, mirror=false,
rotation=0. The 1088-vs-1080 discrepancy is vendor pixel scaling, not padding
or crop. Composite face→analysis (1280×720) transform: scale_x = 2/3,
scale_y = 0.6617647.

Evidence: `reports/cam03_20260907_geometry_verification/`
(`cam03_face_recording_geometry_validation.json`, extracted frame,
`artifact_sha256.json`).

## Profile: configured per existing contract

`configs/p6s_cam03_offline_identity.profile.json` was generated with the
existing `P6SCapabilityProfile.create` (self-hashing; content hash
`v1:sha256:00c59479…94cdd`). Nothing was copied from cam02's validation scope:

- device_serial / device_model (`T5AI-W/IPG-5950PCS-AI`) / firmware
  (`3.4.0-20260430-EN`) come from cam03's own persisted push metadata
  (`push_coverage_observations`, source `device_push_metadata`).
- face_recording geometry fields come from this session's cam03 validation
  above, referencing its report.
- push_coverage_status=verified references the fixed Run's complete Coverage;
  historical_mapping references the September 7 preflight Event-Time evidence.
- Acceptance policy is the formal `device-recognition v1` at threshold 0.75
  (hash `v1:sha256:0e4ae36a…89aea`). The on-site 0.6 diagnostic override was
  NOT written into the profile or any default.
- Shared policy/report refs that are genuinely device-family scoped
  (pagination unsupported, guarded-interior event time, segment boundary
  unverified) keep their existing refs; cam02's geometry validation ref was
  not reused.

The path is supplied via `RETAILVISION_CAPABILITY_PROFILE_PATH` as declared in
`configs/behavior_cameras.cam03.identity.json`; the camera config file itself
was not modified.

## Formal identity re-run (identity stage only)

`run_cam03_run_identity.py` was run WITHOUT `--diagnostic` against the fixed
Run (database opened `mode=ro + query_only`):

```text
output            = reports/cam03_identity_formal_20260907_212202/
mode              = qualified_profile; profile resolved; geometry supplied
threshold         = 0.75 (formal default, no override)
coverage          = complete; 5 records evaluated, 1 accepted (0.75773224)
timeline          = verified (recomputed from 4745-frame PTS evidence)
spatial match     = profile-mapped face bbox falls inside Track 1's person
                    bbox at seq 492 / PTS 21.8 s (containment 1.0,
                    face_time_delta 0 s); geometry_verified=true
Track 1           = confirmed, member_id = member-gaojunjie
other 7 tracks    = unmatched
confirmed facts   = 6 (both guitars: PICK_UP/HOLDING/PUT_DOWN)
anonymous facts   = 0
publication_eligible = false on every fact
formal BehaviorEvent writes = 0
```

The confirmed status was produced by the existing aggregation rule after the
geometry gate was satisfied — probable was not manually promoted. The earlier
diagnostic artifacts (`cam03_20260907_144722_identity*`,
`cam03_identity_rerun_20260907_204402`) are retained unchanged as the separate
probable-status record.

## Remaining boundary

- Formal BehaviorEvent publication remains out of scope and was not attempted;
  the identity CLI is not a publisher and every fact stays
  `publication_eligible=false`.
- The member MCP tools will not see these events until a separate, authorized
  publication step exists.
- `full_segment_boundary_status` remains `unverified` (existing scoped
  contract value); the profile does not claim it.
- No test suite was run this session; no historical test counts are claimed.
