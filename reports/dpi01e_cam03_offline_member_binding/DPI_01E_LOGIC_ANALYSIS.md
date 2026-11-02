# DPI-01E cam03 会员身份绑定逻辑审计

## 当前结论

本阶段没有查询 Recognition Records，也没有写入会员 BehaviorEvent。原因是录像绝对时间来源尚未形成可信、无冲突的确认记录。当前 12 条 DPI-01D Stage9 事件已冻结并保留为匿名行为事实。

## 现有链路

1. `RecordedCameraRecognitionProvider` 按 `[recording_started_at, recording_ended_at]` 做完整分页查询，并在暴露证据前验证分页完成、设备范围、时间范围、Recognition Acceptance Policy 和 Historical Identity 映射。
2. `GuardedOfflineIdentityPass` 接收完整查询结果，再由 `GuardedOfflineIdentityAssociator` 执行事件时间 Gate、Face bbox 坐标转换、同摄像头时间窗口和空间关联。
3. `OfflineSameCameraIdentityAssociator` 只绑定同一 `logical_camera_id` 下的 Person Track，不执行跨摄像头 ReID；多个候选、时间或空间不满足时保持匿名/不可用。
4. `OfflineTrackIdentityAggregator` 按 `person_track_token` 聚合证据。只有唯一完整证据才是 `confirmed`；冲突进入 `ambiguous/track_conflict`，无记录是 `unmatched`，查询或时间不完整是 `unavailable`。
5. `OfflineBehaviorIdentityProjector` 仅允许 `confirmed` 结果携带 `member_id`。其它结果仍输出匿名行为事实，不删除商品行为事实，也不能写正式会员 BehaviorEvent。

## 当前 DPI-01E Gate

| Gate | 结果 | 说明 |
| --- | --- | --- |
| cam03 logical camera | 通过 | 使用 `cam03`，不使用物理 IP 作为业务 camera_id |
| 源视频完整性 | 通过 | SHA-256 与 DPI-01D 产物一致 |
| PTS/时间基 | 通过 | 复用 DPI-01D 的真实 PTS 和 time_base |
| 录像绝对时间 | 阻塞 | 画面叠印与文件名/容器 metadata 冲突，尚未确认权威来源 |
| Recognition Records 查询 | 未启动 | 时间 Gate 未通过，避免错误时间范围查询 |
| 同摄像头身份关联 | 未启动 | 没有可接受的 Recognition evidence |
| 会员行为投影 | 阻塞 | 不生成 `member_id` 或正式 BehaviorEvent |

## 时间证据

视频首帧叠印为 `2026-09-04 16:34:52`，中间样本约为 `16:35:30`、`16:36:09`，末帧约为 `16:36:10`。这与文件名 `2026_09_05_20_33_56.mp4` 和容器 creation metadata `2026-09-05T12:34:02Z` 不一致，因此当前只记录为候选 `video_timestamp_overlay_only`，未将其升级为可信 recording interval。

## 可继续条件

确认以下三项后才能执行 Recognition 查询和同摄像头关联：

- `recording_started_at` 的完整 ISO-8601 时间；
- `recording_ended_at` 或确认按真实 PTS 加视频时长计算；
- IANA timezone（当前画面候选为 `Asia/Shanghai`）。

确认后仍必须完整分页查询并逐页审计；查询为空只能得到 `unmatched`，查询失败或分页不完整只能得到 `unavailable`，不能自动升级为会员确认。
