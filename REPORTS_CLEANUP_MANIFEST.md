# Reports Cleanup Manifest

日期：2026-09-18。执行：MOVE ONLY；报告内容未修改，未删除文件。
`reports/` 的历史运行输出统一归类 HISTORICAL_RUNTIME_EVIDENCE，不是当前系统 Authority。

## Summary

审计 40 个目录、413 个产物（377,776,529 bytes），另有 reports/README.md 导航文件。
归档 25 个目录、180 个文件、148,772,812 bytes（141.88 MiB），其中包含 1 个此前已空的 closure-review 目录。
保留 15 个目录、233 个原始证据文件（229,003,717 bytes），另保留更新后的 README。
完整目录作为验证包移动，避免拆散同 Run 的媒体、日志和哈希；目录名只帮助发现候选，不是最终归档依据。

归档根：`D:/GJJ/work/retailvision_archive/20260918_repo_cleanup/reports/`。
每个归档目录保持同名。执行前全库文件哈希、Git 状态、报告内容摘要、引用扫描、数据库路径检查、
逐文件原路径/目标路径/大小/SHA256/Git 状态/原因位于相邻 `reports_cleanup_audit/`。
`manifest.json` 给出全部 413 份产物的 KEEP/ARCHIVE 决定；`verification.json` 给出执行后核验。
原先 Markdown 归档在 reports/reports/ 的历史映射保持不变，本轮不覆盖已有归档。

## Kept

以下全部属于有保留原因的历史证据，并未晋升为业务 Authority。README 是导航，不算运行产物。

| reports/ 下目录 | 文件数 | 保留原因 |
| --- | ---: | --- |
| `cam03_20260906_behavior_validation` | 91 | 两个现存诊断脚本直接读 replay/full_replay.json 和 validation_summary.json；保留完整证据包及初始化 provenance |
| `cam03_20260907_144722_identity` | 19 | 当前 cam03 capability profile 引用 identity_report.json 的 Coverage 验证 |
| `cam03_20260907_144722_run` | 15 | render_cam03_member_demo.py 默认读取该固定 Run 的 tracks、Stage9 与原始 PTS；身份诊断也引用其 manifest |
| `cam03_20260907_geometry_verification` | 3 | 当前 cam03 capability profile 引用几何验证报告 |
| `cam03_20260907_onsite_prepare` | 5 | 当前 cam03 capability profile 引用 face preflight 作为历史 mapping 验证 |
| `cam03_20260907_publication_qualification` | 4 | 保留自动发布未通过的独立证明：manual source、录像索引、录像窗口 registration coverage 和 device/video clock 门槛 |
| `cam03_20260908_manual_import` | 3 | 保留曾写入当前 demo 数据库的单次手工导入审计、精确回滚范围与旧表修复记录；不能以普通 smoke 输出归档处理 |
| `cam03_identity_formal_20260907_212202` | 17 | render_cam03_member_demo.py 默认读取正式 association；保留绑定 provenance |
| `dpi01e_cam03_offline_member_binding` | 11 | 旧 12-event 样本的未确认时钟冲突仍独立存在；后续不同视频不自动修复该证据。两个脚本仅把本目录当输出，不是保留主因 |
| `p6s_device_fingerprint_20260819` | 4 | 身份 readiness 脚本可选收集 scoped profile 的原始证据，保留可追溯性 |
| `p6s_face_recording_geometry_20260819` | 11 | qualify_p6s_device_fingerprint.py 直接读取 Event 701 identity association；readiness 可选收集 geometry probe |
| `p6s_trusted_publication_20260820` | 20 | 商品 analytics 回归测试直接读取 Event 804 behavior_probe_flush.json；同包还保留未关闭的 containment/正式发布资格原始证据 |
| `r5_04_2_minimal_autonomous_answer_gate_20260826_recovery1` | 10 | 保留尚无关闭证明的具体 summary/compare grounding 失败样本；不同 recovery 不是完全相同证据 |
| `r5_04_2_minimal_autonomous_answer_gate_20260826_recovery2` | 10 | 保留尚无关闭证明的 trend/display/hot-products grounding 失败样本 |
| `r5_04_2_minimal_autonomous_answer_gate_20260826_recovery3` | 10 | 保留最后验收中的 grounding、presentation 和 MCP_TOOL_CALL_FAILED 独立失败证据 |

## Archived

下表每项目标都是外部归档根下的同名目录；逐文件映射与 SHA256 见 reports_cleanup_audit/manifest.json。

| 原目录（reports/ 下） | 文件数 | 归档原因 |
| --- | ---: | --- |
| `cam03_20260906_video_preview` | 4 | 旧预览；无代码/配置/测试输入，非未关闭问题唯一证据 |
| `cam03_20260907_144722_identity_formal-review` | 19 | 旧 blocked 阶段复核；现有独立几何、正式 association 和 publication qualification 覆盖当前结论 |
| `cam03_20260907_144722_preview` | 26 | 旧初始化预览；当前配置与固定 Run 已独立保存，未被当前代码当输入 |
| `cam03_20260907_cli_identity` | 11 | 旧独立 integration Run 诊断；同批 source Run 一起归档，匿名/历史 mapping 边界已在当前文档 |
| `cam03_20260907_cli_identity_final` | 11 | 旧诊断重复结果；没有新的当前合同或唯一未关闭证据 |
| `cam03_20260907_cli_integration_run` | 15 | 旧 CLI 集成产物；消费它的旧诊断一并归档，现用 demo 固定 Run 不动 |
| `cam03_20260907_formal_closure_review` | 0 | 空目录；原 REVIEW.md 已在前一阶段归档。本轮仅移动目录，不删除 |
| `cam03_20260907_identity_readiness` | 8 | mapping 重复检查后已 CURRENT_MAPPING_READY；旧样本匿名限制已有保留原始证据与当前指南 |
| `cam03_20260907_profile_reuse_audit` | 3 | 旧 profile/path 排查；当前 profile、geometry 与原始验证输入保留 |
| `cam03_20260907_threshold_experiment` | 2 | 无 identity 提升的 counterfactual 输出；脚本只向此处写入，正式阈值不改变 |
| `cam03_20260908_demo_video` | 7 | 已被 PTS 修复版本替代的演示输出；完整保留用于历史比对 |
| `cam03_20260908_demo_video_pts_fixed` | 14 | 已完成的演示视频生成与逐帧验证；脚本对此目录仅输出，真实输入另行保留，PTS 结论提升到 Runbook |
| `cam03_20260908_session_diff` | 3 | 会话 diff/patch 备份；不是当前源码、未被构建应用，不执行其中命令 |
| `cam03_20260908_upload_binding` | 2 | 上传视频副本与 source binding 审计；非生产/fixture 输入，源 Run 与身份包保留 |
| `cam03_identity_rerun_20260907_204402` | 17 | 旧未确认诊断已由固定 Run formal identity 证据接续；未作为现有脚本输入 |
| `local_git_sync_20260908` | 11 | 旧 Git 同步输出/patch；不改变当前仓库或其他 worktree |
| `mb01_controlled_fixture_20260827_run1` | 2 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `mb01_controlled_fixture_20260827_run2` | 2 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `mb01_controlled_fixture_20260827_run3` | 2 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `mb01_controlled_fixture_20260827_run4` | 2 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `mb01_controlled_fixture_20260827_run5` | 2 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `mb01_controlled_fixture_20260827_run6` | 2 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `mb01_controlled_fixture_20260827_run7` | 2 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `mb01_controlled_fixture_20260827_run8` | 3 | 受控临时 fixture 的历史执行输出；没有测试把它当 fixture 输入。run8 原始判定与独立 policy review 分开保存，当前规则提取至 MCP 指南 |
| `r5_04_2_minimal_autonomous_answer_gate_20260826` | 10 | JVM AF_UNIX 启动失败的历史批次；后续 recovery 已恢复执行，回答验收仍未关闭的样本保留 |

## Referenced

- 生产 agent/pipelines 中未发现直接硬编码读取这些 reports 目录的路径；当前 cam03 capability profile
  有 3 个 validation_report_ref 字符串。这些是 provenance 引用，不等于运行时逐个打开报告；仍保留对应 3 包证据。
- test/test_product_interaction_analytics.py 直接读取 Event 804 behavior_probe_flush.json，原位置与整个包保留。
  没有发现 tests 把 mb01_controlled_fixture 的输出作为 fixture 输入，8 个输出目录均可归档。
- audit_cam03_identity_readiness.py / probe_cam03_recognition_threshold.py 读取旧 cam03 replay；
  render_cam03_member_demo.py 读取固定 Run/identity；qualify_p6s_device_fingerprint.py 读取 Event 701；
  diagnose_event_804_spatial_identity.py 读取 Event 804。输入包均保留，脚本未改。
- threshold experiment、Demo 视频、DPI01E 的部分脚本引用仅为输出目录，目录不必预先存在；
  此类引用不当作读取依赖。DPI01E 因未关闭时钟问题另行保留。
- AGENTS / README / ADR 没有指向本轮已移走产物的必须阅读链接；此前清理计划与 JSON 清单中的历史路径保持历史含义。
- 4 个 runtime 数据库以 mode=ro、query_only 扫描声明为 TEXT/CHAR/JSON/CLOB 的字段，
  未发现引用本轮 reports 目录名称的记录；未更改数据。该检查不声称覆盖未知外部库或 BLOB 内编码路径。
- 归档/保留报告中的相互引用和旧绝对路径不改写；按 manifest 查找归档位置。历史 hash 指向原 bytes，不能改为当前文档 hash。
- 未启动服务、模型、设备或外部 Agent；未批量执行报告中保存的脚本/SQL/patch。

## Canonicalized

| 长期结论 | 当前文档 | 原始证据处理 |
| --- | --- | --- |
| cam03 confirmed 不等于 trusted publication；manual source、registration、clock 仍有门槛 | [CURRENT_STATUS](docs/CURRENT_STATUS.md) | qualification 与正式 identity 包保留 |
| 6 条行为来自一次单独授权的 manual import，不是 identity CLI 自动发布；历史表修复不构成新 migration 指令 | [CURRENT_STATUS](docs/CURRENT_STATUS.md) | 手工导入 audit/结果/回滚范围保留 |
| 原始 PTS 解码与 CFR 展示映射必须一致；不先自动补帧再按原帧索引渲染 | [Offline runbook](docs/runbooks/offline-analysis.md) | 两版 Demo 视频及已完成验证原样归档 |
| MB01 原始 exact-single-tool BLOCKED 与独立 read-only-composition review PASS 分开；fixture 不证明真实会员验收 | [MCP 指南](docs/product_analytics_mcp_server.md)、CURRENT_STATUS | 全部受控 fixture 输出归档，原始判定不重打分 |
| R5-04.2 回答验收仍阻断；历史 JVM socket 失败与 Provider/MCP 业务失败分开 | [MCP 指南](docs/product_analytics_mcp_server.md) | 最初进程失败批次归档；3 组未关闭回答失败 evidence 保留 |
| DPI01E 旧 12-event 样本的时钟来源未确认，后续不同视频不能替代 | [CURRENT_STATUS](docs/CURRENT_STATUS.md) | 唯一旧样本时钟冲突证据保留 |

## Unresolved

1. Event 804 containment/正式发布资格未关闭；真实 regression fixture 和诊断来源不动。
2. cam03 自动发布仍受来源、segment index、旧窗口 registration 和 device clock 约束；单次手工导入不是通用豁免。
3. DPI01E 12-event 样本绝对时间资格未解决。
4. R5 三组 recovery 的具体失败不同，未发现逐项关闭证据，因此没有只留最后一组或擅自判定前两组已无价值。
5. 当前少量诊断/渲染脚本仍依赖固定 reports 路径；将其改成独立 fixture 或外部参数是后续任务，本轮不为清空目录改业务代码。
6. 已存储事实的手工导入原始审计继续保留；不是自动可丢弃的 smoke。

## Restore and verification

外部 reports_cleanup_audit/restore_reports.ps1 默认只检查并预览；显式 -Apply 才复制回仓库。
目标已存在则终止，不覆盖新生成的同名 Run。恢复前先核对原路径和 SHA256，不按文件名合并不同 Run。
归档清理不改变既有生产资格或测试失败基线；本轮只运行受影响的 fixture/profile 回归，不把历史全库结果当重跑。

实际核验结果：180 个归档文件和 233 个保留产物 SHA256 全部匹配；无非预期修改、丢失或新增文件。
代码、配置、数据库、模型、测试和 ADR 均保持原始字节。121 个本地 Markdown 链接有效。
Event 804 商品统计回归与 cam03 profile 测试共 12 passed，仅保留原有 SQLAlchemy 弃用警告。
25 个目录的恢复预览通过；未实际恢复、未提交 Git、未执行数据库迁移或设备/模型任务。
