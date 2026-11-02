# 当前状态

更新：2026-10-28（仅补充经营MCP及云端候选结果）。本页汇总当前代码能力、已记录验证和未解决事项。
既有视觉状态沿用2026-09-18记录，本轮未合并旧视觉优化提交，也没有提高其生产资格。

## 经营100问预置数据验收（2026-10-27）

- 2026-10-28本地审查修复：独立退换货率榜单、明确零销量补齐、配置缺省不分类、临期批次日期、
  显式题目domain映射已实现。经营26项、原商品/会员MCP34项回归通过；外部Java Store103项通过。
  原快照100报表只读检查通过且SHA未变。候选镜像 `business-review-fixes-20261028` 已构建并通过
  隔离容器MCP检查和源码/JAR哈希核对；随后完成下述云端独立候选测试，正式入口和本地页面未更新。
  独立候选快照 `runtime/databases/store-business-20261027-reviewfix.db` 已持久化临期批次日期，
  覆盖期与种子沿用原样本，1,459,324条记录；只读容器100报表验证通过，原快照未改动。

- 云端初版100问为99/100；第59题“顾客流失预警”被误归feedback，模型多次向customers传入59失败。
  题号及统计不变，仅将显式domain改为customers；修复后28项经营MCP回归通过，云端三次重复均成功。
  事实限制以 `Public fact interpretation contract:` 声明，Java最终回答器保留此说明而排除工具路由，
  相关Java30项回归与打包通过。最终候选 `business-review-r2-20261028` 经真实deepseek-flash
  完整100问自动检查100/100（中位3.680秒、最大8.314秒），候选内100报表MCP检查通过。
- **人工语义验收未通过**：新品回答仍用“常见新品观察期”自设判断；库存回答曾称其他商品可售天数
  都低于10天，与保温杯17.4304天矛盾。未知门店安全拒绝但提示为通用工具失败。不能以100/100接口
  成功证明全部答案正确。当前仅服务器loopback 19012候选，正式Store未替换；原26个容器身份/镜像/
  启动时间、生产配置和原经营库未变。证据在Root仓库
  `tmp/store-business-100q-20261027/cloud-review-20261028/r2/`，失败/诊断批次保留在云端release。

- 新增独立 `agent.analytics.store_business` 数据生成/只读统计服务与
  `python -m agent.mcp_server.business_server`，10类工具覆盖用户100问表。
  使用综合零售（食品及配送）场景；客流等也由预置数据提供，不运行摄像头算法。
- 独立 `runtime/databases/store-business-20261027.db` 包含3家门店、1,451,638条按日及维度的指标记录，
  覆盖2025-08-27至2026-10-27。保留内部synthetic来源与批次，未接真实POS/ERP，未写原商品/会员库。
  服务不自动续期；超出覆盖范围不能补造今日数值。
- 17项经营统计回归通过；原商品MCP的18项检查通过。外部Java候选镜像结合真实deepseek-chat模型，
  最新一轮100/100题通过成功响应、实际领域工具调用和协议输出检查。此前失败批次保留。
  已抽查部分金额、比率、门店与日期口径；自动检查不证明所有自然语言分析或建议正确。
- 2026-10-27已备份并更新云端Store，实际deepseek-flash候选100/100调用检查及正式HTTPS鉴权链路通过。
  随后修复会员MCP说明将全店范围误写为相机ID、遗漏会员独立时间窗的问题，未修改数据库或放宽范围校验。
  商品/会员MCP相关39项回归、云端8次原问题与组合问答、经营10领域抽查以及正式HTTPS连续/流式问答通过。
  当前云端镜像为 `business-100q-member-scope-20261027`；100/100对应基础镜像，补丁只做上述定向回归。
  本地证据位于Root仓库 `tmp/store-business-100q-20261027/` 的 `live-release/`、`release.json`；
  部署步骤见Root仓库 `docs/DEPLOYMENT.md`。本节不改变既有视觉算法的生产资格。

## 系统与入口

RetailVision 将 WHAT（行为）、WHO（身份）、WHICH VISIT（到访）与后续 Effects 分开。
匿名行为可以成立；会员身份只能由合格的 confirmed 证据提供。

| 能力 | 当前入口 / 实现 | 边界 |
| --- | --- | --- |
| 门店实时装配 | `run_store_runtime.py` → `pipelines/store_runtime.py` | new/legacy 显式选择、Store lease、启动门槛、fatal/shutdown |
| HTTP / 会员管理 | `run_member_api.py` → `agent/api/member_api.py` | API、会员注册与运营查询；启用前检查模式与数据库 |
| 组件运行 | `run_behavior_runtime.py`、`run_device_identity.py` | 仍有调试/兼容用途，不是待删副本 |
| Offline V1 | `scripts/run_offline_behavior_analysis.py` → `pipelines/offline_behavior_pipeline.py` | source/PTS/identity/artifact/publication 分层校验 |
| cam03 采集与身份诊断 | `scripts/run_cam03_evidence_capture.py`、`scripts/run_cam03_run_identity.py` | 前者 artifact-only；后者只读，不构造 publisher |
| Staff Greeting | `scripts/run_staff_greeting_offline.py` | 正式 Core、逐帧 PTS、artifact-only；演示另在 `demo/greeting/` |
| 商品与会员 MCP | `python -m agent.mcp_server.server` | 默认关闭、只读、schema-ready-only、可信 scope |
| 对外 Agent | 外部 mewcode-java 经 MCP 调用 | 本仓库 Python Harness 只用于测试/演示；本轮未检查外部部署版本 |

物理位置看 [模块布局](architecture/CURRENT_MODULE_LAYOUT.md)，逻辑所有权看
[v2 架构](architecture/v2/README.md)，业务语言看 [CONTEXT](../CONTEXT.md)，决策看 [ADR](adr/)。
v3 保留为未来设计，不能按其历史阶段状态重复执行已完成的整理。

## 已实现的数据能力

- SAM3.1 Person/Product → Stage8 关联 → Stage9 PICK_UP/HOLDING/PUT_DOWN 已有实现。
  `scripts/run_offline_complete_loop_golden.py` 的共享分析器仍被 cam03 使用。
- `agent/analytics/product_interaction_analytics.py` 已有 ProductInteractionFact writer/query；
  ORM 表为 `product_interaction_facts`，与正式 `behavior_events` 分开。
- MCP 提供 7 个商品工具和 3 个会员行为工具；会员 summary/timeline/recent 只读已确认的公开行为。
  recent 默认 100、最大 200；商品查询有 limit 时默认 10、最大 100。
- 商品与会员可用独立数据库及可信 scope，窗口最多 31 天。商品查询当前接受无时区后缀的本地 ISO 时间；
  UTC 统一是 [时间合同说明](repository/TIMESTAMP_CONTRACT_NOTE.md) 中的待办，不是已实现能力。
- MCP 默认关闭，schema migration 是显式独立命令；数据库缺失时不能悄悄创建。
  匿名统计不能推断会员、购买、销售额或转化率。公开返回不得包含 raw identity、Face bbox 或内部 token。
- 完整视觉互动报告需组合匿名商品查询与已确认会员行为，并分别说明 scope；不能把匿名互动归到某个会员。
  外部 Agent 的历史实现记录不构成本轮部署验收。

详细启动、工具和 grounding 合同见 [MCP 指南](product_analytics_mcp_server.md)。

## 验证范围与生产资格

| 范围 | 已记录结果 | 保留限制 |
| --- | --- | --- |
| Event 804 / cam02 | 两次完整互动、6 facts、621/621 实际采样，行为 PASS | Primary containment 0.7388888889 < 0.8，identity unmatched；B2.8 trusted publication NOT VERIFIED，Provider A fail-closed |
| Event 701 / cam02 | 历史实机 confirmed Track Identity 已证明链路存在 | 不能升级 Event 804 的不匹配结果 |
| P6S 时间与录像 | 已验证限定注册区间、Push Inbox、5 秒 guarded interior、几何与 scoped profile | Full Segment Boundary NOT VERIFIED；cam02 历史 QueryRecordList 不支持 |
| cam03 / 9 月 7 日 | 独立几何/profile 闭环，固定 Run Track1 confirmed | 只读 identity CLI 没有发布正式 BehaviorEvent；不能套用 cam02 profile |
| 会员查询 / 9 月 10 日 | 历史只读 MCP 观察到 6 条合格公开事件 | 不能据此称先前诊断 CLI 发布成功或 Event 804 已放行 |
| Staff Greeting | 单段视频 Demo ready，1808/1808 帧、1 次 confirmed；标定 provisional | tracking fragmentation、多视频质量基线、正式标定与 Offline 前端消费仍待完成，非 production ready |
| 外部 Agent R5-04.2 | CODE_COMPLETE / ACCEPTANCE_BLOCKED；三次 recovery 为 18/21、17/21、17/21 | 仍有自主回答验收失败；不宣称生产启用，也不把旧 R5-04.1 当最新结果 |

cam03 现场曾明确设置接收阈值 0.6，仅是那个进程/测试的覆盖值；默认和正式策略 0.75 未被修改。
历史记录不代表设备、端口或服务此刻仍处于当时状态。现场执行前按 [cam03 指南](cam03_new_sample_recording.md) 检查。

## 冻结合同

- 保留 Stage5/7C/8/9、阈值与 authoritative Presentation Time；不补假帧、复制末帧或编造时间。
  常规 provider window 为 12 contexts；EOF terminal window 仅处理剩余真实采样，至少一个 fresh sample。
- 一次真实闭合互动产生 pickup、holding interval、put-down 三个事实。同人同商品的两次真实互动不能被误去重。
  EOF / Track Lost 不得合成 PUT_DOWN 或完整 holding。
- Primary 先按合法时间距离选择再评价几何；support snapshot 仅用于诊断，不能挑重叠最大的一帧代替 Primary。
- confirmed / probable / ambiguous / conflict / unmatched / unavailable 保持独立；probable 不通过投票变为 confirmed。
  只有 confirmed 能投影正式 member_id，其余必须为 null。
- 正式发布仍需同 Run 的来源字节、历史注册、profile、Push Coverage、PTS/guarded clock、历史身份、confirmed Track、
  几何、事实校验、artifact hash/seal 和 publication_eligible 全部满足。job-level publication fence 防重发，不吞第二次真实互动。
- Offline V1 不创建 Visit、Arrival、Welcome、Coupon、live binding 或 EvidenceClip；正式离线事件的 visit_id、
  entry_event_id、evidence_clip_id 保持 null。候选会员与 Face bbox 只在受限诊断中。
- Store lease、身份 provenance、已接受 ADR、Schema 和生产 gate 不因文档整理改变。

## 自动化基线与待办

最近完整基线来自此前目录整理，记录在 [validation.json](repository/cleanup_20260918/validation.json)：

| 检查 | 整理前 | 整理后 |
| --- | --- | --- |
| Python | 1462 passed、8 failed、4 skipped、1 collection error | 1472 passed、同样 8 failed、4 skipped、0 collection error；276 subtests |
| 前端 | 46 passed | 46 passed，build 成功，保留 chunk 警告 |
| 布局与 Profile Demo | — | 32 passed |

8 个已知失败：1 个 cam03 缺 associations fixture；4 个 legacy device E2E canonical event lookup；
2 个 DurablePushInbox mapping/boundary；1 个 clipped-person Staff Role。它们未被隐藏或改成 xfail。
本轮仅核验文档、引用及文件完整性，不把历史结果表述为本轮重跑。

后续独立工作：上述失败、Greeting 多视频/标定/性能与只读结果消费、外部 Agent 验收、
legacy 消费者退休评估、缺失 ADR 状态核对。不能用本页自动开启这些业务变更。

## 文件与历史

相机 JSON 在 `configs/`，SQLite 在 `runtime/databases/`，模型在 `data/models/`。
`outputs/` 保留的视频、Run 产物与 372 张被本地 demo 数据库引用的会员截图不是无用缓存；
截图路径供已记录事件/证据展示与追溯，删除前必须协调引用和留存策略。
原数据库、模型、训练标签、证据 JSON 和其他工作树均不属于本轮移动对象。
历史资料及恢复规则见 [归档索引](repository/ARCHIVE_INDEX.md)。后续更新本页当前事实；决策写 ADR；
一次性过程与报告完成后进入外部归档，不再建立无限增长的 WORKLOG_INDEX。

## Reports 审计补充事实（2026-09-18）

`reports/` 中的运行记录属于 HISTORICAL_RUNTIME_EVIDENCE，不是架构或业务 Authority。
本次后续阶段允许归档已完成且无活动读取依赖的 JSON、日志和媒体；配置、fixture 与未关闭问题证据保留。

- cam03 固定 Run 的 6 条 confirmed 行为在 9 月 8 日曾由单次明确授权手工导入 demo 数据库，
  不是 identity CLI 自动发布。该次 audit 还记录旧表 nullable 合同修复及精确回滚范围；
  这只是既往操作记录，不授权重复导入、运行回滚或修改 Schema。
- 同一 Run 的自动 trusted publication 仍未通过：manual export 来源未获 controlled P6S source 资格，
  缺设备录像 segment index，后续 registration 不覆盖旧录像窗口，device/video clock 未验证。
  profile、PTS 或 Track confirmed 的单项通过不能替代完整发布资格。
- DPI-01E 的另一段旧 12-event 视频仍有 filename/container/OSD 时钟冲突，绝对时刻与会员投影未获资格；
  后来的 6-event 视频或手工导入不关闭该问题。
- cam03 Demo 的变帧时序播放问题已记录 PTS 修复验证；渲染使用原始 PTS，不重新推理或修正设备时钟。
- MB-01 受控 fixture 的 run8 原始 exact-single-tool 验收 BLOCKED，独立 related-read-only-composition
  policy review 为 PASS；原结果没有被改写。这不等同真实会员或外部 Agent 的完整验收。

上述限制和未关闭事项的原始证据留在仓库，归档/保留逐项目录清单见
[REPORTS_CLEANUP_MANIFEST](../REPORTS_CLEANUP_MANIFEST.md)。
