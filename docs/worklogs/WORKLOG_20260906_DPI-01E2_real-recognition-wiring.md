# WORKLOG 20260906 DPI-01E.2 real Recognition wiring correction

Status: `CONFIGURATION LANDED / HISTORICAL CAM03 EVIDENCE UNAVAILABLE`

## Correction

The earlier artifact-only report remains retained at
`reports/dpi01e_cam03_offline_member_binding/` for traceability. This
correction changes the runner so a valid injected/formally loaded configuration
executes the existing Recognition Provider and identity chain through
`GuardedOfflineIdentityPass`, `OfflineSameCameraIdentityAssociator`,
`OfflineTrackIdentityAggregator`, and `OfflineBehaviorIdentityProjector`.

The recording end is now derived from the replay's authoritative
`behavior_summary.media_duration_seconds` (`81.241` seconds), with the replay
PTS status and time-base fields retained as evidence. It no longer computes
duration as `source_frame_count * time_base`. `ZoneInfo("Asia/Shanghai")` is
used for the operator-confirmed local start, and all 12 event PTS values are
verified inside `[start_at, end_at)`.

## Current execution result

- Frozen source SHA-256: `921f6ec3881a8222ee269c767a6a3bab276e95be337b95272a0ad53a9afd2920`
- Frozen replay SHA-256: `a08d40c2203a2148da8c57b1a973aa56120be684932a28d9c311556dfeece10d`
- Frozen behavior events: 12
- Query window: `[2026-09-04T08:34:52.000, 2026-09-04T08:36:13.241)` UTC-naive
- Execution chain: code-connected and covered by simulated integration tests
- Real cam03 Recognition configuration: unavailable
- Real Recognition request: not executed
- Confirmed member behavior events: 0
- Formal database writes: 0

The formal configuration loader now uses the explicitly configured behavior
camera file through `load_behavior_camera_configs`; it does not scan arbitrary
JSON files. The current environment is missing the cam03 camera entry,
Recognition password/unique code, and capability profile, while its device
scope still redacts to the legacy cam02 value. These missing items are reported
without secret values.

## Verification

The run-level simulated integration tests cover provider invocation, complete
empty query to `unmatched`, incomplete query fail-closed behavior,
confirmed/ambiguous projection, independent Person Tracks, unchanged 12-event
fields, and no formal write. The focused suite passes `53/53`.

This does not claim real member association completion, publication, or
production validation. A real query requires the approved cam03 camera/store
mapping, Recognition device/service/auth configuration, and verified capability
profile to be supplied on site.

## Configuration intake correction

The operator supplied the cam03 device mapping separately from the repository
configuration. The runtime configuration entry points remain `agent/config.py`
environment variables plus the explicitly selected behavior-camera JSON loaded
by `load_behavior_camera_configs`; no arbitrary JSON is scanned. The supplied
device uses unauthenticated active Push delivery, so the DPI-01E.2 formal
configuration audit now accepts `recognition.auth_mode=none` with an empty
password and records the mode without exposing credential values. This does not
create or approve a cam03 capability profile, change the frozen time window, or
execute Recognition. The real query remains blocked until the formal cam03
 camera/store mapping and verified `p6s_cam03` capability profile are present.

## Cam03 configuration and historical evidence audit

The formal non-secret mapping is now committed at
`configs/behavior_cameras.cam03.identity.json` and was loaded through the
normal `BEHAVIOR_CONFIG_PATH` path. It resolves to logical `camera_id=cam03`,
`store_id=store-1`, device `cam03`, the supplied service address, and
`auth_mode=none` / `evidence_mode=durable_push_inbox`. `PEAK_ENTRY_CAMERA_ID`
was not changed.

The command-line runner now performs a read-only Coverage/Inbox preflight when
`DATABASE_URL` is present. Against the frozen half-open UTC window
`[2026-09-04T08:34:52.000, 2026-09-04T08:36:13.241)`, cam03 coverage returned
`heartbeat_end_missing` and the window contained zero Recognition rows. The
historical audit is therefore `HISTORICAL_RECOGNITION_EVIDENCE_UNAVAILABLE`,
not `unmatched`. The persisted cam03 Push metadata matches the supplied model
and firmware, but the existing capability resolver has no cam03 profile and
returns `profile_not_found`; no cam02 profile was renamed or reused.

Result: configuration mapping landed and the normal entry path passed; real
Provider identity querying did not execute because historical Coverage is not
provable and the cam03 capability profile is absent. Both Person Tracks remain
`unavailable`, with zero confirmed member behavior facts and zero database
writes. The restricted result is retained in
`reports/dpi01e_cam03_offline_member_binding/capability_profile_audit.json`.
