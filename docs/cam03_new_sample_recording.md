# cam03 采集与身份诊断

本页是操作说明；历史 Run 结果见 [当前状态](CURRENT_STATUS.md)，原现场过程见
[归档索引](repository/ARCHIVE_INDEX.md)。不根据旧日志判断当前监听端口、注册状态或服务状态。

## 采集前

1. 检查已有 8081 listener，避免重复接收器；录像全程和结束后的 heartbeat 要有 Push Coverage。
2. 使用现有会员授权/同步流程，核对当前 mapping 及 Event-Time 历史 revision；不能回填旧视频历史。
3. 检查有效的 device-store、camera-store、device-camera registration（现场配置 cam03 / store-1）。
4. 正式非 diagnostic 身份运行需要适用 cam03 profile，通过 `RETAILVISION_CAPABILITY_PROFILE_PATH`
   或相机配置加载；cam02 校准不能直接冒充 cam03 verified profile。cam03 独立几何验证已存在，
   不要按早期“没有 profile”的日志重新设计模块，也不能假设旧 profile 适合移动后的相机。
5. 新视频必须重新做 source-bound product 初始化与 mask review；不复用旧 source 的 confirmation。

## 接收器

```powershell
& D:/GJJ/work/code/scripts/start_cam03_push_receiver.ps1
```

默认阈值 0.75。历史现场曾显式传 `-SimilarityThreshold 0.6`，仅作用于该次进程；
它不是正式默认值或新的身份资格规则。不要为追求通过而临时放宽身份门槛。
launcher 复用本地持久 fingerprint，使用当前 camera JSON 与已有 schema；仅允许 inbox/health/Coverage
写入，禁止 migration 和 BehaviorEvent 写入，不修改 PEAK_ENTRY_CAMERA_ID。结束后 Ctrl+C。
不要把旧 general-service uvicorn 命令混入这个受限接收流程。

只读 mapping 检查（device credentials 使用既有安全配置，不写入报告）：

```powershell
$check = 'D:/GJJ/work/code/reports/cam03_mapping_check_' + (Get-Date -Format yyyyMMdd_HHmmss) + '.json'
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe scripts/bootstrap_cam03_member_history.py --camera-config configs/cam03/behavior_cameras.cam03.identity.json --database runtime/databases/demo_flow.db --output $check
```

`--apply-current` 是显式写操作，只用于开始已核实缺失的当前 revision；不提供历史生效时间。
Offline loader 优先级：显式 CLI path → RETAILVISION_CAMERA_CONFIG_PATH → BEHAVIOR_CONFIG_PATH。
Settings 读取进程环境，不假设 `.env` 自动生效；不要改旧 Run 的 video/config 来伪装新样本。

## 录像与分析

先用一名会员完成 face camera → pickup → hold → put back；确认 NEW Recognition 在当前批准策略下
accepted，且能在 Event Time 解析。保留录像起止 OSD、设备时钟和 Push event 关联；文件名不是时钟证据。
身份事件两侧至少保留 5 秒，录像后保留 Coverage heartbeat。多人样本应分别识别，不能推断同时出现者同身份。
使用新视频完成 source-bound 商品初始化后，提供实际配置与确认文件路径：

```powershell
$video = Read-Host 'New MP4 absolute path'
$productConfig = Read-Host 'New source reviewed product-camera JSON'
$confirmation = Read-Host 'New source initialization_confirmation.json'
$initDir = Read-Host 'New source initialization evidence directory'
$localStart = Read-Host 'Confirmed local start in ISO format, including +08:00'
$timeSource = Read-Host 'Recording time confirmation evidence/reference'
$runOutput = 'D:/GJJ/work/code/reports/cam03_run_' + (Get-Date -Format yyyyMMdd_HHmmss)
& C:/Users/Administrator/miniconda3/envs/sam3/python.exe D:/GJJ/work/code/scripts/run_cam03_evidence_capture.py --video $video --output $runOutput --analyzer-root D:/GJJ/work/retailvision_cam03_probe_20260906_b45 --product-config $productConfig --initialization-confirmation $confirmation --initialization-dir $initDir --store-id store-1 --local-start $localStart --time-source $timeSource
```

Outputs include `person_tracks.jsonl`, `product_tracks.jsonl`,
`raw_stage9_events.jsonl`, `source_run_manifest.json`, overlay and SHA-256 manifest.
New captures also retain `source_timeline_evidence.json`: original ffprobe PTS
AND actual frame durations, scoped to source hash and Run. The existing sampler
checks continuity without a bypass. Missing durations are not filled using FPS
or neighboring PTS. Identity recomputes the timeline assessment from these
hashed observations; older Runs without the evidence remain timeline-unavailable.
Sampling gaps, PTS integrity and declared absolute start/clock qualification are
reported separately. A long real frame may cross sample targets without proving
PTS corruption; a valid IANA/UTC conversion does not prove device/video clocks
are synchronized.
The entry is artifact-only: it does not itself execute identity or publish facts.
The retained analyzer root is an explicit local dependency; preserve its recorded
export-only patch. That dependency does not authorize replacing current product algorithms.

## 只读身份诊断

在当前 API 环境运行，避免外部两吉他 analyzer 的 import 混入 identity runtime：

```powershell
$runOutput = Read-Host 'Completed independent capture Run directory'
$identityOutput = 'D:/GJJ/work/code/reports/cam03_identity_' + (Get-Date -Format yyyyMMdd_HHmmss)
& C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe scripts/run_cam03_run_identity.py --run-dir $runOutput --output $identityOutput --database runtime/databases/demo_flow.db --camera-config configs/cam03/behavior_cameras.cam03.identity.json --diagnostic
```

`--diagnostic` 允许已有 unqualified durable-inbox 路径，不覆盖 profile/geometry 算法门槛。
非 diagnostic 模式缺少 profile 会阻断。现有 Guarded pass、spatial associator、Track aggregator、
normalizer 和 projector 被复用，不构造 publisher。输出是受限诊断，不是公开脸图或正式行为发布。
每次 Run 有独立 token/facts；不能塞回旧 hardcoded 12-event DPI-01E 或六事件 replay。
九月六日旧视频的低于阈值/无历史 mapping 结果不会因九月七日 revision 或新 Run confirmed 自动修复。
合法 IANA/UTC 转换不等于设备与视频时钟同步；OSD 差异不能靠挑 best-overlap frame 隐藏。
