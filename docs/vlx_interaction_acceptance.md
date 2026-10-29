# VLX 拿起／持有／放下：独立测试验收交付

## 当前保存状态

本次两份 VLX 结果 **尚未入库**。原始输出在各 Run 的 `result.json`、
`events.jsonl`、`observations.jsonl` 和 `manifest.json`；标注视频是文件
`annotated.mp4`。两份 manifest 都明确 `writes_database: false`。
本次交付和导出工具不连接数据库、不创建数据库、不运行迁移，不修改原结果。
这只是对这两个文件输出 Run 的确认，不是对未知云端数据库的检查。

| 原片文件名 | 最终结果目录名 | 摄像头显示名称 | 完整操作数 |
| --- | --- | --- | --- |
| `2026_10_28_01_39_57.mp4` | `test_20261028_013957_v9` | 行为检测-3 | 9 |
| `2026_10_28_01_51_25.mp4` | `test_20261028_015125_v8` | 行为检测-1 | 6 |

摄像头显示名称来自此前对话对应关系，不代表已经核验数据库中的 camera_id。
实际源视频 SHA256、时长、Run 标识、原 result.json 的 SHA256 在 JSON 中。
本机完整位置另列在本次交付旁的 `LOCAL_LOCATIONS.md`，不进入分发 ZIP。

## 交付内容及来源

- `data/interaction_facts.json`：完整交换文件，包含 runs、sessions、facts、totals。
- `data/interaction_facts.jsonl`：相同 facts 的逐行格式，便于独立测试库导入。
- `data/<analysis_run_id>/result.json`：原生统计结构的脱敏副本，保留每个物品、
  每位用户、每个用户＋物品的统计；只允许匿名业务字段，路径仅保留文件名。
- `data/<analysis_run_id>/metadata.json`：对应录像和结果来源、时间基准和缺失元数据。
- `EXPECTED_BEHAVIORS.md`：可见操作的预期计数、当前识别时间和逐项人工验收表。
- `source/`：可合入原 RetailVision 项目的源文件快照、配置示例和测试。
- `SOURCE_FILES.json`：源码用途、路径、校验值及与原视频生成版本的对应情况。
- `VERIFY.json`、`SHA256SUMS.json`：本次核验结果与包内各文件校验值。
- `verify_bundle.py`：只读核验包内哈希、事实数、持续时间和录像间 ID 隔离。

不包含 `.env`、真实密钥、会员库、数据库文件、人脸模板、模型权重、原录像、
标注录像或全画面截图。回归测试仅带匿名几何轨迹、匿名服饰颜色特征，以及两张
检查过的物品颜色裁剪图，无可识别人脸或会员信息。原视频仍在原目录。

当前目录没有 Git 元数据，因此提供文件快照和 SHA256，不提供虚构的 commit/diff。
六个核心文件与原视频的 `delivery_validation.json` 所记录哈希一致。关联依赖也
随包提供，避免仅复制入口后漏模块；无需替换无关业务模块或现有数据库。

本批结果使用 **已保存的 VLX／姿态检测证据＋新运行的 SAM3.1 跟踪／语义检查**，
并重新计算匿名关联和互动状态，不是重新跑完整 VLX 推理，也不是纯 VLX 单模型。
源码入口支持对新视频运行 VLX，再运行 SAM3.1；这次交付过程不重新推理。

## 交换 JSON 字段

编码 UTF-8；schema_version 为 `vlx_acceptance_relative_facts_v1`。
所有 `*_seconds` 单位都是秒。时间基准为源视频相对展示时间戳 PTS，不能用帧号
除以固定 FPS 替代，也不能把文件名中的时间直接当作真实录像开始时间。

| 层级／字段 | 类型 | 含义 |
| --- | --- | --- |
| `schema_version` | string | 交换结构版本，与原生 result 的版本分别管理 |
| `exported_at_utc` | ISO string | 本次导出时间，不是行为发生时间 |
| `writes_database` | boolean | 本工具固定 false |
| `production_import_ready` | boolean | 当前固定 false；不代表识别结果为空 |
| `missing_production_metadata` | string[] | 生产入库仍缺的门店、相机和可信绝对起点 |
| `runs` | object[] | 每份源视频和分析结果的元数据 |
| `sessions` | object[] | 每次完整或不完整的拿起操作，一次抛接仍是一次 session |
| `facts` | object[] | 导出的标准事件名称和相对时间事实 |
| `totals` | object | Run 数、session 数、fact 数、按事件类型计数、已完成持有总秒数 |
| `analysis_job_id` | string | 按源视频哈希确定的上传来源任务标识 |
| `analysis_run_id` | string | 源视频哈希＋原报告哈希确定的运行标识；相同报告重导出不变 |
| `source_run_name` | string | 本机原结果目录的末级名称 |
| `source_video_filename` | string | 原片文件名；不含本机目录 |
| `source_video_id` / `source_sha256` | string | `sha256:` 来源标识／完整原片 SHA256 |
| `original_result_sha256` | string | 脱敏前 result.json 的原始字节校验值 |
| `duration_seconds` | number | 原片时长 |
| `camera_name` / `camera_name_basis` | string/null | 对话中的相机显示名和来源说明 |
| `store_id` / `camera_id` | null | 尚未核验真实部署 ID，不冒用显示名 |
| `recording_started_at` | null | 未核实的录像绝对起始时刻 |
| `database_status` | string | `not_written_by_this_run`，只说明该 Run 未写库 |
| `evidence_provenance` | object | 推理来源：是否新跑 VLX、SAM3.1、复用证据哈希、参与分组 |
| `interaction_session_id` | string | 加了 Run 前缀的 session ID，用于跨视频安全导入 |
| `source_session_id` | string | 原生 ID，例如 `white_guitar:1`，仅在原 Run 内唯一 |
| `product_key` / `product_name` | string | 固定物品实例的内部键／展示名；不是已对接的商品 SKU |
| `anonymous_user_key` | string | Run＋匿名编号；同号用户在不同录像不能合并 |
| `user_id` / `user_label` | integer/string | 录像内轨迹编号／“用户N”；不代表真实身份或会员 |
| `time_basis` | string | 固定 `source_video_pts_seconds` |
| `started_at` / `ended_at` | null | 预留绝对时刻；测试数据不伪造生产日期 |
| `attribution_uncertain` | boolean | 用户归属是否存在不确定性；不能当识别概率 |

### Session 专属字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `pickup_seconds` | number | 识别出的拿起起点 |
| `pickup_confirmed_seconds` | number | 累积证据后确认拿起的时刻，可能晚于起点 |
| `putdown_seconds` | number/null | 识别出的放下起点；缺失时不伪造 |
| `putdown_confirmed_seconds` | number/null | 确认放下的时刻 |
| `holding_seconds` | number/null | 完整操作：putdown_seconds − pickup_seconds |
| `status` | string | 当前两份结果均 completed；不完整记录不能算已完成 |
| `end_reason` | string | 当前 completed 为 observed_release；视频结束/丢失不是放下证据 |
| `last_observed_seconds` | number | 该 session 最后更新的观察时刻 |
| `observed_holding_seconds` | number | last_observed_seconds − pickup_seconds；含确认尾段，不能替代 holding_seconds |
| `observation_gaps` | integer | 记录到的观察缺口次数，不能直接换算成缺失秒数 |
| `source_gap_return_recoveries` | integer | 源观察中断后的返回恢复次数 |

### Fact 专属字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `fact_id` | string | Run＋session＋event_type 的确定性 SHA256，重导出幂等键 |
| `event_type` | string | merchandise_pick_up / merchandise_holding / merchandise_put_down |
| `started_at_seconds` / `ended_at_seconds` | number | 拿起／放下为相同时点；持有为完整区间 |
| `confirmed_at_seconds` | number | 拿起用拿起确认时刻；持有/放下用放下确认时刻 |
| `holding_duration_seconds` | number/null | **仅 holding 行填写**，其他两种为 null，防止时长重复求和 |

一次完整 session 导出三行事实。本批合计 15 次完整 session、45 行 facts，
三类各 15 行；持有总时长 179.778 秒。原生 `events`/`events.jsonl` 也包含
PICK_UP、HOLDING、PUT_DOWN 三种事件，本批分别为 27 条和 18 条，合计 45 条。
HOLDING 是放下确认后记录的一段完整区间，不是每个持有帧写一条。
不完整 session 只导出已确认拿起，不补造完整持有区间或放下。

### 原生 result.json 的其余字段

`schema_version=vlx_interactions_v1`、`interaction_policy=instance_semantic_continuity_v8`。
`qualification=implementation_output_requires_visual_review` 表示还需要使用方人工验收。
`by_object`、`by_user`、`by_user_object` 和 `total` 共享以下统计字段：
pickup_count、putdown_count、completed_count、incomplete_count、total_holding_seconds、
average_holding_seconds、max_holding_seconds。后三个只统计完整持有；无完整记录时
总时长为 0，平均和最大值为 null。by_object 保留未参与该录像的固定物品的 0 行。
`object_key/object_name` 对应交换格式的 `product_key/product_name`。
`users` 是被观察到的匿名轨迹清单（含未操作物品的轨迹），其中 first_seen_seconds、
last_seen_seconds、observations、appearance_matched_reentries 分别为首次/末次观察、
观察次数和外观连续性关联的重入次数；这些都不构成会员身份。
原生 events 中 event_id、event_type、session_id、object_key、object_name、user_id、
user_label、timestamp_seconds、confirmed_at_seconds 表示状态事件及确认时间；
HOLDING 事件另含 ended_at_seconds（放下起点）和 duration_seconds（完整持有时长）。
原生事件 ID 仅 Run 内唯一；导出前会核验事件与 session 的用户、物品、时刻和时长。

## 独立测试库导入口径

导入 `facts` 或 JSONL，不要再把 sessions 和原生 events 作为同一事实重复插入。
建议以 fact_id，或 `(analysis_run_id, interaction_session_id, event_type)` 为唯一键；
相同键相同内容可跳过，内容不同需报告冲突。按事件类型计拿起/放下次数，
只累加 merchandise_holding 行的 holding_duration_seconds。按 anonymous_user_key
汇总用户，保留 user_label 展示；不按单独的 user_id 跨录像关联。

这些是**测试交换记录**，不是已经符合生产 ORM 必填要求的数据库行。
现有 product_interaction_facts 的 started_at、ended_at、store_id、camera_id、
track_key_hash 等有约束；当前没有经过核验的绝对起点和部署 ID，也没有部署侧
HMAC 匿名轨迹哈希。不要将 anonymous_user_key 冒充正式 track_key_hash，
不要用文件名或导出时间补生产时刻。测试库可先保存相对秒数和 Run 元数据。
以后接云端时再复用项目正式归档、匿名投影、时间校验和幂等流程；不在本包中
关闭校验、开启会员发布或改变 Store 的只读挂载。

拿起后持续抛接算一次；确实放下再拿起才开始下一次。
不限制必须在桌面，但当前验收仅覆盖这两段录像、这六个固定实例，不代表任意
摄像头、任意新物品都已通过。没有记录“放置后静置多久”这一独立统计字段。

## 源码使用与验证

先在现有项目的测试副本中比较合入 `source/agent`、`source/scripts` 和 `source/test`。
配置以 `.example.yaml` 形式提供，复制为自己的配置后填写模型、Python、字体位置。
不要将示例覆盖线上配置。运行入口是 `scripts/run_vlx_interaction_refined.py`；
旧纯 VLX/CSRT 入口仍是 `scripts/run_vlx_interaction_video.py`，两者效果不能混同。

```powershell
# 仅校验交付内容，无需模型、GPU或数据库。
python verify_bundle.py

# 合入现有项目后运行匿名行为回归。
python -m pytest -q test/test_vlx_interaction_video.py test/test_vlx_interaction_recovery.py test/test_vlx_interaction_tracking.py test/test_vlx_acceptance_export.py

# 对新的原始输出导出交换数据，目标目录必须不存在。
python scripts/export_vlx_acceptance.py --run "原结果目录1" --run "原结果目录2" --output-dir "新的JSON输出目录"

# 模型环境准备好后，对新录像完整运行；不读写数据库。
python scripts/run_vlx_interaction_refined.py --video "录像.mp4" --output-dir "新的识别输出目录" --config config/vlx_interaction_refined.local.yaml --classes 3,4,5
```

第二段玩偶录像的 classes 是 `0,1,2`；第一段是 `3,4,5`。初始帧需能唯一定位目标，
否则当前实现会报错；不要把预期次数或下文参考时刻作为识别输入。
回放已有证据使用 `--recorded-run`，须自行保留原 run 的 manifest、observations
和对应源视频，包内的脱敏业务结果不包含这些完整推理输入，不能直接当回放输入。
完整推理需 VLX-Seek、WeDetect、YOLO pose、SAM3.1 及匹配 GPU 运行环境，模型不随包。
软件版本观测值另列 `RUNTIME_VERSIONS.json`，不是自动安装锁文件。
导出器和 verify_bundle.py 只依赖 Python 3.11+ 标准库；业务回归还需 pytest、numpy、
OpenCV contrib、Pillow、PyYAML 和可用的 ffmpeg/ffprobe。
