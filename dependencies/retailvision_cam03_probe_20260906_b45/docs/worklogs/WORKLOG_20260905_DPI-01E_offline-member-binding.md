# Worklog: DPI-01E cam03 Offline Member Binding

Date: 2026-09-05

Status: `BLOCKED / RECORDING_TIME_PROVENANCE_PENDING`

## Scope

审计现有 Offline Recognition、同摄像头空间关联和行为身份投影链路，复用 DPI-01D 的冻结产物，不重新运行 SAM3，不修改商品 Track、Stage8、Stage9 或 12 条 Stage9 事件。

## Inputs

- Source video: `D:/GJJ/work/video/2026_09_05_20_33_56.mp4`
- Source SHA-256: `921f6ec3881a8222ee269c767a6a3bab276e95be337b95272a0ad53a9afd2920`
- DPI-01D replay: `D:/GJJ/work/dpi01d_cam03_20260905_203356_review/full_replay_rerun2.json`
- DPI-01D diagnostics: `D:/GJJ/work/dpi01d_cam03_20260905_203356_review/full_diagnostics_rerun2/dpi01d_window_diagnostics.json`

## Findings

- `RecordedCameraRecognitionProvider`、`GuardedOfflineIdentityPass`、`OfflineSameCameraIdentityAssociator`、`OfflineTrackIdentityAggregator` 和 `OfflineBehaviorIdentityProjector` 已满足本阶段复用要求。
- DPI-01D 的 12 条事件已保留为匿名行为事实，两个 Person Track token 未被修改。
- 视频叠印首帧为 `2026-09-04 16:34:52`，末帧约为 `2026-09-04 16:36:10`。
- 该叠印与文件名 `2026_09_05_20_33_56.mp4` 及容器 creation metadata `2026-09-05T12:34:02Z` 冲突。
- 因此未查询 Recognition Records，未生成任何 `confirmed` member binding，也未写正式 BehaviorEvent。

## Artifacts

目录：`reports/dpi01e_cam03_offline_member_binding/`

- `DPI_01E_LOGIC_ANALYSIS.md`
- `source_time_manifest.json`
- `recognition_query_audit.json`
- `recognition_page_audit.jsonl`
- `identity_evidence.jsonl`
- `track_identity_associations.jsonl`
- `member_bound_behavior_facts.jsonl`
- `anonymous_behavior_facts.jsonl`
- `identity_gate_report.json`
- `member_behavior_summary.json`
- `artifact_sha256.json`

## State

```text
DPI_01E_IDENTITY_BINDING_BLOCKED
IDENTITY_EVIDENCE_UNAVAILABLE
BEHAVIOR_REPLAY_REMAINS_VALID
RECORDING_TIME_PROVENANCE_PENDING
```

继续执行所需的唯一外部确认是录像绝对起止时间和 IANA timezone。确认后才可完整分页查询 Recognition，并将结果按 `confirmed/unmatched/ambiguous/unavailable` 投影；当前 12 条商品行为事实保持不变。
