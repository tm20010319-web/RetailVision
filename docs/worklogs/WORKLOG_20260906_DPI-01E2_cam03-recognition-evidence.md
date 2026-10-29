# WORKLOG 20260906 DPI-01E.2 cam03 Recognition evidence

Status: `BLOCKED / CAM03_RECOGNITION_CONFIGURATION_UNAVAILABLE`

Correction note: the original time calculation described below is superseded
by `WORKLOG_20260906_DPI-01E2_real-recognition-wiring.md`. The corrected runner
uses the replay's `media_duration_seconds` and retains this worklog and its
artifacts only for traceability.

## Scope

This phase consumes only the frozen cam03 recording and
`full_replay_rerun2.json`. No SAM3, product tracking, Person Track tokens,
Stage8, Stage9, event semantics, or confidence thresholds were changed.

## Frozen input verification

- Source: `D:\GJJ\work\video\2026_09_05_20_33_56.mp4`
- Source SHA-256: `921f6ec3881a8222ee269c767a6a3bab276e95be337b95272a0ad53a9afd2920`
- Frozen replay SHA-256: `a08d40c2203a2148da8c57b1a973aa56120be684932a28d9c311556dfeece10d`
- `raw_stage9_events`: 12
- Product/person scope: `ai_electric_guitar -> Person Track 2`; `ai_guitar -> Person Track 1`
- Replay state mismatch: 0

The original artifact used the replay time-base fields as an implementation
hint. The corrected runner uses the authoritative replay duration. The
resulting half-open UTC-naive window is
`[2026-09-04T08:34:52.000, 2026-09-04T08:36:13.241)` and retains the
operator-confirmed `Asia/Shanghai` source time.

## Recognition gate

The current environment exposes the legacy `cam02` device scope and lacks the
approved cam03 camera/store mapping and required cam03 secret configuration.
Only presence and redacted identifiers are written to the audit. No
Recognition request was attempted and no database session/write was opened.

Both Person Tracks are independently `unavailable`; no member ID was assigned.
All 12 behavior facts remain anonymous and publication-ineligible. The gate
codes are:

```text
DPI_01E_TIME_CONFIRMED
DPI_01D_BASELINE_12_EVENTS_FROZEN
CAM03_RECOGNITION_CONFIGURATION_UNAVAILABLE
DPI_01E_IDENTITY_BINDING_BLOCKED
```

## Implementation and verification

- Added artifact-only runner: `scripts/run_dpi01e2_cam03_offline_member_binding.py`
- Added output directory: `reports/dpi01e_cam03_offline_member_binding/`
- Hardened existing recorded Recognition pagination against duplicate pages.
- Corrected recorded and durable-push queries to the required `[start_at, end_at)` boundary.
- Added focused frozen-input, time, scope, pagination, and identity projection tests.
- Focused result: `48 passed` across DPI-01E.2 plus Offline identity/behavior tests.

This phase does not record `DPI_01E_RECOGNITION_QUERY_PASS`,
`DPI_01E_OFFLINE_MEMBER_ASSOCIATION_PASS`, `MEMBER_BEHAVIOR_PUBLISHED`,
`PRODUCTION_VALIDATED`, or `DPI_01E_FULLY_CLOSED`.
