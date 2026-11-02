# 文档治理执行计划

日期：2026-09-18。只读审计已完成，用户已授权“执行后续计划”。本文件记录已批准范围，不授予新的业务开发权限。

## 审计范围与数量

121 份 Markdown：根目录 3、docs 104、reports 5、其他 9；103 已跟踪、18 未跟踪。
工作记录 50 份加索引 1 份；当前交接 1 份；ADR 20 份；架构文档 13 份。
分类：CANONICAL 12、ACTIVE_REFERENCE 22、HISTORICAL_WORKLOG 51、GENERATED_REPORT 10、ADR 20、DUPLICATED_SUPERSEDED 6。
目录外的 requirements、158 个训练标签和 2 个测试日志不作为普通历史文档移动。
排除 Git 元数据、独立工作树、环境、依赖、IDE 和缓存，不改其他工作树。

## KEEP / ARCHIVE / MERGE / REVIEW

- KEEP：领域语言、20 份 ADR、当前物理布局、v2 逻辑合同、操作说明和仍有效的 Greeting 风险。
- ARCHIVE：67 份已分类历史记录；旧交接在提取当前事实后归档，合计 68 份。只移动，不销毁。
- MERGE：交接与最新工作记录归入 CURRENT_STATUS；长期 Greeting 规则归入 domains/staff-greeting；MCP/cam03 操作指南去除阶段日志；旧文件映射由 CURRENT_MODULE_LAYOUT 与 source_moves.json 替代。
- REVIEW：v3 仍为 DESIGN_ONLY，不能因版本号较大成为当前实施指令。ADR 0007–0018 缺显式状态，不擅自补 accepted/superseded。真实生产资格、外部 Java Agent 版本与原有 8 个测试失败不在本轮修复。
- LEGACY CODE：只标注 ACTIVE / COMPATIBILITY / LEGACY_CANDIDATE / EXPERIMENTAL / UNKNOWN，建议 KEEP 或 REVIEW；没有足够证据直接删除源代码。

## 执行与恢复

执行前保存全部范围内文件哈希、121 份原始 Markdown 副本、Git HEAD 和含未跟踪文件的状态。
外部归档：`D:/GJJ/work/retailvision_archive/20260918_repo_cleanup/`。
按已确认分类移动，保留原相对路径。MANIFEST.md / manifest.json 记录类别、原因、引用、替代文档、Git 状态、哈希、时间与恢复路径。
移动后更新当前入口和链接；历史证据 JSON 哈希、ADR、源代码、配置和数据不重写。
最终核验移动前后哈希、活动链接、脚本/测试引用和未修改文件，清除本轮移动形成的空文档目录。

## 逐文件分类

下表是执行前完整清单。原交接在执行时转为 HISTORICAL_HANDOFF，其他分类保持审计结论。详细入链位于外部 audit_inputs.json。

| 原路径 | 分类 | 动作 |
| --- | --- | --- |
| `AGENTS.md` | CANONICAL | KEEP_UPDATE |
| `CONTEXT.md` | CANONICAL | KEEP_UPDATE |
| `README.md` | CANONICAL | KEEP_UPDATE |
| `configs/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `data/models/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `demo/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `demo/camera_profile/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `demo/greeting/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/README.md` | CANONICAL | KEEP_UPDATE |
| `docs/STAFF_VEST_ANNOTATION_GUIDE.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/adr/0001-unified-recognition-intake.md` | ADR | KEEP |
| `docs/adr/0002-use-device-recordings-for-evidence-clips.md` | ADR | KEEP |
| `docs/adr/0003-mount-display-cameras-for-behaviour-observation.md` | ADR | KEEP |
| `docs/adr/0004-retain-seven-days-of-device-recordings.md` | ADR | KEEP |
| `docs/adr/0005-single-member-association-resolver.md` | ADR | KEEP |
| `docs/adr/0006-treat-association-outcome-as-rebuildable-projection.md` | ADR | KEEP |
| `docs/adr/0007-separate-finalization-workflow-from-binding-application.md` | ADR | KEEP |
| `docs/adr/0008-separate-spatial-association-clock-from-business-event-clock.md` | ADR | KEEP |
| `docs/adr/0009-represent-track-identity-conflicts-with-terminated-bindings.md` | ADR | KEEP |
| `docs/adr/0010-follow-exact-track-lifecycle-for-confirmed-bindings.md` | ADR | KEEP |
| `docs/adr/0011-project-durable-binding-provenance-onto-behaviour-events.md` | ADR | KEEP |
| `docs/adr/0012-recover-durable-bindings-by-exact-application-identity.md` | ADR | KEEP |
| `docs/adr/0013-treat-compatible-track-reconfirmation-as-no-op-application.md` | ADR | KEEP |
| `docs/adr/0014-project-track-identity-through-one-exact-runtime-cache.md` | ADR | KEEP |
| `docs/adr/0015-fence-unterminated-confirmed-bindings-by-exact-track-token.md` | ADR | KEEP |
| `docs/adr/0016-fail-stop-store-runtime-on-persistent-finalization-invariants.md` | ADR | KEEP |
| `docs/adr/0017-project-finalization-terminal-outcomes-onto-recognition-inbox.md` | ADR | KEEP |
| `docs/adr/0018-compose-finalization-and-binding-heartbeat-under-one-capability-gate.md` | ADR | KEEP |
| `docs/adr/0019-refresh-canonical-visit-from-confirmed-track-activity.md` | ADR | KEEP |
| `docs/adr/0020-converge-exact-anonymous-entrance-visits-before-finalization.md` | ADR | KEEP |
| `docs/agents/domain.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/agents/issue-tracker.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/agents/triage-labels.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/architecture/CURRENT_MODULE_LAYOUT.md` | CANONICAL | KEEP_UPDATE |
| `docs/architecture/v2/DEVELOPMENT_GUIDE_V2.md` | CANONICAL | KEEP_UPDATE |
| `docs/architecture/v2/FACT_OWNERSHIP_V2.md` | CANONICAL | KEEP_UPDATE |
| `docs/architecture/v2/MODULE_DEPENDENCY_RULES_V2.md` | CANONICAL | KEEP_UPDATE |
| `docs/architecture/v2/MODULE_MAPPING_V2.md` | DUPLICATED_SUPERSEDED | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/architecture/v2/MODULE_MIGRATION_PLAN_V2.md` | DUPLICATED_SUPERSEDED | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/architecture/v2/PHASE_MODULE_MAPPING.md` | DUPLICATED_SUPERSEDED | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/architecture/v2/PROJECT_MODULE_ARCHITECTURE_V2.md` | CANONICAL | KEEP_UPDATE |
| `docs/architecture/v2/README.md` | CANONICAL | KEEP_UPDATE |
| `docs/architecture/v2/TEST_ARCHITECTURE_V2.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/architecture/v3/MODULE_MAPPING_V3.md` | ACTIVE_REFERENCE | REVIEW_KEEP |
| `docs/architecture/v3/MODULE_MIGRATION_PLAN_V3.md` | ACTIVE_REFERENCE | REVIEW_KEEP |
| `docs/architecture/v3/TARGET_MODULE_ARCHITECTURE_V3.md` | ACTIVE_REFERENCE | REVIEW_KEEP |
| `docs/authority/DOCUMENT_AUTHORITY_REGISTRY.md` | CANONICAL | KEEP_UPDATE |
| `docs/cam03_new_sample_recording.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/handoff/RETAILVISION_CURRENT_HANDOFF.md` | CANONICAL | MERGE_THEN_ARCHIVE |
| `docs/offline_v1_module_mapping.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/product_analytics_mcp_server.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/reports/R5_04_2_MINIMAL_AUTONOMOUS_ANSWER_GATE_REPORT.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `docs/repository/CLEANUP_INVENTORY.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `docs/repository/DATABASE_AND_DATA_ASSET_INVENTORY.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `docs/repository/DOCUMENT_AUTHORITY_MATRIX.md` | DUPLICATED_SUPERSEDED | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/repository/OUTPUTS_CLEANUP_20260918.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `docs/repository/PROJECT_CLEANUP_AUDIT_20260917.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `docs/repository/REFACTOR_R4_05_DOCUMENT_AUTHORITY_AUDIT.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `docs/repository/REPOSITORY_CLEANUP_PLAN.md` | DUPLICATED_SUPERSEDED | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/repository/STAFF_GREETING_V1_REMAINING_RISKS_AND_HARDENING_20260831.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/repository/TIMESTAMP_CONTRACT_NOTE.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `docs/worklogs/WORKLOG_20260824_product-analytics-v1.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-01_product-analytics-qualification.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-02_product-analytics-controlled-enablement.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-03-1-1_acceptance-evidence-closure.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-03-1-2_acceptance-evidence-integrity.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-03-1-3_agent-grounding-contract-stabilization.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-03-1_canary-readiness-hardening.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-03-2-1_deterministic-business-answer-stability.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-03-2_controlled-business-answer-enrichment.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260825_R5-03_store-intelligence-agent-integration.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260826_R5-03-2_autonomous-mcp-baseline-restoration.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260826_R5-04-1_autonomous-mcp-semantic-integrity.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260826_R5-04-2_minimal-autonomous-answer-gate.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260826_R5-04_autonomous-mcp-canary-reliability.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260826_R5-04_followup-observability.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260827_MB-01A_member-behavior.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260830_staff-greeting-offline-replay.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260831_staff-greeting-chinese-overlay.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260831_staff-greeting-confidence-semantics.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260901_DPI-01A_dense-product-initialization.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260904_DPI-01B_video-preflight.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260904_DPI-01C1_cam03-config-and-surface.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260904_DPI-01C1_two-guitar-real-probe.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260904_DPI-01C2_real-propagation.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260904_DPI-01C_real-sam3-adapter.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01C2_action-window-diagnostic.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01C3_fixed-display-position.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01C3_upright-display-state.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01D1_ai-guitar-putdown-diagnosis.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01D_full-behavior-replay.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01D_new-video-initialization-review.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01D_tracking-loss-fix.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260905_DPI-01E_offline-member-binding.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260906_DPI-01E2_cam03-recognition-evidence.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260906_DPI-01E2_real-recognition-wiring.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260906_cam03-video-member-validation.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-cli-identity-integration.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-face-preflight.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-formal-identity-review.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-formal-profile-geometry-closure.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-handoff-state-verification.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-identity-evidence-readiness.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-live-threshold-060.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-new-action-diagnostic-run.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-onsite-preparation.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-profile-reuse-audit.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260907_cam03-threshold-experiment.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260908_cam03-demo-pts-render-fix.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260910_recent-member-behavior-mcp.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_20260918_repository-layout-cleanup.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `docs/worklogs/WORKLOG_INDEX.md` | HISTORICAL_WORKLOG | ARCHIVE_AFTER_CONSOLIDATION |
| `reports/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `reports/cam03_20260906_behavior_validation/VALIDATION_REPORT.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `reports/cam03_20260907_formal_closure_review/REVIEW.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `reports/cam03_20260907_profile_reuse_audit/REUSE_AUDIT.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `reports/dpi01e_cam03_offline_member_binding/DPI_01E_LOGIC_ANALYSIS.md` | GENERATED_REPORT | ARCHIVE_AFTER_REFERENCE_REPAIR |
| `runtime/databases/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `skill/skill.md` | DUPLICATED_SUPERSEDED | ARCHIVE_AFTER_CONSOLIDATION |
| `test/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
| `tools/README.md` | ACTIVE_REFERENCE | KEEP_UPDATE |
