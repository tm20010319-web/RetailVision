# 遗留代码候选审计

日期：2026-09-18。仅审计，未删除、移动或重构源代码。
AST 扫描 340 个 Python 文件，无解析错误；检查实际 import、根入口、CLI、测试、配置和动态加载。
静态可达表示可能存在路径，不代表分支在生产实际执行；无静态入链不等于废弃。
runtime/test 列为静态图结果，CLI 和顶层手工执行另列。`pipelines.__getattr__` lazy 导出会扩大静态候选路径。
所有候选建议仅 KEEP 或 REVIEW；本轮没有满足安全删除证明的 REMOVE_LATER。

| path | classification | purpose | references | runtime_reachable | test_reachable | replacement | confidence | recommended_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent/legacy/detector.py | COMPATIBILITY | 视频检测器：YOLO目标检测 + 简单追踪 | demo.legacy_cli, pipelines.legacy.historical_video_pipeline, pipelines.legacy.realtime_entry_pipeline, test.test_detector_logic, test.test_profile_async, test.test_realtime_device_identity_mode, test.test_realtime_entry_pipeline, test.test_realtime_simulation | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| agent/legacy/entry_event_coordinator.py | COMPATIBILITY | Best-effort correlation between YOLO tracks and camera identity events. | pipelines.device_identity_runtime, test.test_device_recognition_end_to_end, test.test_entry_event_coordinator | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| agent/legacy/member_entry_service.py | COMPATIBILITY | Transactional member-entry visit finalization and retry recovery. | pipelines.device_identity_runtime, pipelines.legacy.member_business_runtime, test.test_device_event_worker, test.test_device_identity_app, test.test_device_recognition_end_to_end, test.test_member_business_models, test.test_member_entry_service | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| agent/legacy/_customer_visit_identity.py | COMPATIBILITY | Internal identity attachment rules for entry-backed customer Visits. | agent.visits.customer_entry_visit | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| agent/runtime/stage10_demo.py | ACTIVE | Realtime Stage 10 demo wiring for the frozen Person/Product contracts. | agent.behavior.sam31_realtime_providers, agent.runtime.stage10_product_profile, scripts.run_offline_complete_loop_golden, scripts.run_stage10_demo, test.test_b28_qualification_semantics, test.test_stage10_1_realtime, test.test_stage10_2_1_product_latency, test.test_stage10_2_2_runtime_ready, test.test_stage10_2_performance, test.test_stage10_2_product_preflight, test.test_stage10_3_presentation, test.test_stage10_demo | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| agent/runtime/stage10_presentation.py | ACTIVE | Chinese presentation contracts for the Stage 10 demo surface. | agent.runtime.stage10_demo, test.test_stage10_3_presentation, tools.acceptance.stage10_live_acceptance | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| agent/runtime/stage10_product_profile.py | ACTIVE | Stage 10-only Product SAM latency profiling and artifact aggregation. | agent.behavior.sam31_realtime_providers, test.test_stage10_2_1_product_latency | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| agent/runtime/stage10_runtime_readiness.py | ACTIVE | Stage 10 runtime readiness, Person profiling, and acceptance diagnostics. | agent.behavior.sam31_realtime_providers, scripts.run_stage10_demo, test.test_stage10_2_2_runtime_ready | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| pipelines/legacy/customer_profile_pipeline.py | COMPATIBILITY | CustomerProfilePipeline：顾客穿搭画像管线（P1）。 | demo.legacy_cli, pipelines, pipelines.legacy.member_business_runtime, test.test_customer_profile_pipeline, test.test_device_identity_app, test.test_profile_async | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| pipelines/legacy/historical_video_pipeline.py | COMPATIBILITY | HistoricalVideoAnalysisPipeline：历史视频经营分析管线（P0 核心）。 | demo.legacy_cli, pipelines, test.test_historical_video_pipeline | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| pipelines/legacy/member_business_runtime.py | COMPATIBILITY | Composition and lifecycle for durable member-entry business processing. | pipelines, pipelines.device_identity_runtime, run_device_identity, run_store_runtime, test.test_device_identity_app, test.test_device_recognition_end_to_end | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| pipelines/legacy/realtime_entry_pipeline.py | COMPATIBILITY | RealtimeEntryPipeline：实时进店检测管线。 | demo.camera_profile.runtime, demo.legacy_cli, pipelines, pipelines.device_identity_runtime, pipelines.legacy.customer_profile_pipeline, scripts.run_yolo_stable_live, test.test_device_identity_app, test.test_profile_async, test.test_realtime_device_identity_mode, test.test_realtime_entry_pipeline, test.test_realtime_simulation | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/manual/device_face_capability_test.py | LEGACY_CANDIDATE | H5AI-W-50PSS-AI 人脸识别能力验证测试 | 无静态入链；顶层手工执行待核对 | NOT_PROVEN | NOT_FOUND (static) | 无；保留现入口 | LOW（无静态入链；可能顶层直接运行，不能删除） | REVIEW |
| tools/manual/device_http_smoke.py | EXPERIMENTAL | Read-only smoke tests for the replacement P6S device HTTP API. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| tools/manual/device_recognition_smoke.py | ACTIVE | Safely exercise the deployed device-recognition receiver and inspect one event. | test.test_device_recognition_end_to_end；显式 CLI | EXPLICIT_CLI | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/manual/rtsp_connection.py | EXPERIMENTAL | 手工设备/RTSP 工具，需人工确认用途 | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| tools/manual/rtsp_preview.py | LEGACY_CANDIDATE | 手工设备/RTSP 工具，需人工确认用途 | 无静态入链；顶层手工执行待核对 | NOT_PROVEN | NOT_FOUND (static) | 无；保留现入口 | LOW（无静态入链；可能顶层直接运行，不能删除） | REVIEW |
| tools/manual/test_face_mode.py | LEGACY_CANDIDATE | Quick test: set device to face recognition mode. | 无静态入链；顶层手工执行待核对 | NOT_PROVEN | NOT_FOUND (static) | 无；保留现入口 | LOW（无静态入链；可能顶层直接运行，不能删除） | REVIEW |
| tools/harness/product_analytics/descriptions.py | EXPERIMENTAL | Agent-facing descriptions for Product Analytics MCP tools. | demo.product_analytics_agent.run, test.test_product_interaction_mcp, tools.harness.product_analytics.runtime | NOT_PROVEN | YES (static) | 外部生产 Agent 为 mewcode-java；不替代本地测试 Harness | HIGH（已有调用或公开入口） | KEEP |
| tools/harness/product_analytics/runtime.py | EXPERIMENTAL | Standalone LLM tool-calling loop for Product Analytics queries. | demo.product_analytics_agent.run, test.test_product_analytics_agent_runtime | NOT_PROVEN | YES (static) | 外部生产 Agent 为 mewcode-java；不替代本地测试 Harness | HIGH（已有调用或公开入口） | KEEP |
| tools/harness/product_analytics/tool_calling.py | EXPERIMENTAL | Tool-calling contracts and the Ollama adapter for Product Analytics. | demo.product_analytics_agent.run, test.test_product_analytics_agent_runtime, tools.harness.product_analytics.runtime | NOT_PROVEN | YES (static) | 外部生产 Agent 为 mewcode-java；不替代本地测试 Harness | HIGH（已有调用或公开入口） | KEEP |
| pipelines/__init__.py | COMPATIBILITY | Lazy compatibility exports; new callers import explicit composition modules. | scripts.run_offline_complete_loop_golden, test.test_historical_video_pipeline, test.test_profile_async, test.test_realtime_device_identity_mode, test.test_realtime_entry_pipeline | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_offline_complete_loop_golden.py | ACTIVE | Run the current Offline V1 complete-loop against the A-F Golden MP4 files. | scripts.probe_b28_real_behavior, scripts.probe_dpi01d_cam03_offline, scripts.run_cam03_evidence_capture, test.test_cam03_run_evidence_capture；显式 CLI | YES (static) | YES (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/audit_cam03_identity_readiness.py | EXPERIMENTAL | Read-only readiness evidence for the frozen September 6 cam03 run. | test.test_cam03_profile_loading；显式 CLI | EXPLICIT_CLI | YES (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| scripts/probe_cam03_recognition_threshold.py | EXPERIMENTAL | Read-only threshold counterfactual; never a production qualification or publisher. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| tools/analyze_dpi01c2_action_windows.py | EXPERIMENTAL | Build bounded action-window evidence from a real DPI-01C.2 propagation run. | test.test_dpi01c2_action_windows；显式 CLI | EXPLICIT_CLI | YES (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| tools/build_dpi01e_time_audit.py | EXPERIMENTAL | Build the fail-closed DPI-01E time and identity audit artifact. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| demo/legacy_cli.py | COMPATIBILITY | 门店摄像头 AI 分析 — CLI 演示入口。 | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| run_member_api.py | ACTIVE | Start the member management API server. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/build_stage10_3_showcase.py | ACTIVE | Concatenate verified Stage 10.3 scenario videos into the final showcase. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/create_product_analytics_acceptance_fixture.py | ACTIVE | Create the deterministic anonymous Product Analytics acceptance fixture. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/diagnose_staff_greeting_pair.py | ACTIVE | Diagnostic-only Staff Greeting pair trace and artifact assertion. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/evaluate_stage10_3_scenario.py | ACTIVE | Evaluate one recorded Stage 10.3 A-F scenario without synthesizing events. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/init_face_group.py | ACTIVE | Initialize or verify the device face group for member registration. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/migrate_entry_domain.py | ACTIVE | Apply the additive entry-domain schema migration. | 无静态入链；显式 CLI；migration/repair 受保护，不能因无 import 删除 | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/probe_b28_real_behavior.py | ACTIVE | Run the frozen Stage5/7C/8/9 analyzer on one real B2.8 RecordingFile. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（保留受限资格验证入口） | KEEP |
| scripts/qualify_p6s_face_spatial_geometry.py | ACTIVE | Restricted diagnostic for comparing FaceReco BackgroundImage to a PTS frame. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/repair_member_identity_history.py | ACTIVE | One-time, audit-preserving historical member identity repair. | 无静态入链；显式 CLI；migration/repair 受保护，不能因无 import 删除 | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_greeting_threshold_acceptance.py | ACTIVE | Run the controlled-demo Greeting profile against a real video segment. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_greeting_v2_shadow_replay.py | ACTIVE | Replay a real camera video through V1 and V2 Shadow without writing data. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_offline_behavior_analysis.py | ACTIVE | Run a configured Offline V1 analysis adapter under the frozen gates. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_sam31_behavior_live_worker.py | ACTIVE | Consume live SAM 3.1 frame batches from stdin and run cam02 BehaviorMonitor. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_staff_greeting_offline.py | ACTIVE | Run the production V1 Staff Greeting Core against an MP4 artifact-only. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_store_intelligence_business_answer_smoke.py | ACTIVE | Run the R5-03.2 real-Agent business-answer smoke scenarios once each. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_store_intelligence_business_answer_stability.py | ACTIVE | Observe three independent, one-attempt autonomous MCP Agent batches. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| scripts/run_yolo_stable_live.py | ACTIVE | Continuously run YOLO stable-track detection against a live video source. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/calibrate_dense_products.py | ACTIVE | Interactive Offline-MP4 dense Product first-frame initialization. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/probe_dpi01c2_real_propagation.py | EXPERIMENTAL | Run the bounded DPI-01C.2 real two-product SAM3 propagation probe. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| tools/probe_real_sam3_adapter.py | EXPERIMENTAL | Run the DPI-01C single-product real SAM3 Adapter probe on an Offline MP4. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | MEDIUM（需核对人工运行与设备依赖） | REVIEW |
| tools/render_dpi01d_failure_samples.py | ACTIVE | Render small, artifact-backed videos for the two incomplete DPI-01D cycles. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/training/extract_staff_vest_frames.py | ACTIVE | Extract auditable staff-vest training frames from a video. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/training/predict_staff_vest.py | ACTIVE | Run staff-vest inference on an independent video and export review evidence. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/training/report_staff_vest.py | ACTIVE | Generate the staff-vest baseline report and demo-readiness decision. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/training/train_staff_vest.py | ACTIVE | Validate and train the single-class staff-vest detector. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| tools/training/validate_staff_vest_dataset.py | ACTIVE | Validate the single-class staff-vest YOLO dataset. | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 无；保留现入口 | HIGH（已有调用或公开入口） | KEEP |
| demo/product_analytics_agent/run.py | EXPERIMENTAL | Product Analytics Tool Calling Harness (not the production Agent entrypoint). | 无静态入链；显式 CLI | EXPLICIT_CLI | NOT_FOUND (static) | 外部生产 Agent 为 mewcode-java；不替代本地测试 Harness | HIGH（已有调用或公开入口） | KEEP |

## 后续处理门槛

- `agent/legacy/` 与 `pipelines/legacy/` 仍被运行模式、Demo 和测试消费；先明确兼容停止支持范围。
- Stage10 runtime/provider 和 GoldenMp4Analyzer 被 SAM3/cam03 复用，名字旧不构成退休依据。
- `scripts/run_offline_behavior_analysis.py` 的 module:callable、SAM3 provider 外部加载、Stage10 与校准工具的动态 import
  不能仅靠零入链过滤；动态调用位置保存在外部 audit_inputs.json。
- 手工 device/RTSP 脚本可能控制真实设备，不为验证“是否有用”而批量运行。
- CLI、migration/repair、fixture、配置入口、兼容导出、跨测试 helper 都要单独核对后再决定下一轮删除。
- 代码/测试引用、Git 历史和当前调用图保存在外部审计快照；此前移除的实现不在本轮重新删除。
