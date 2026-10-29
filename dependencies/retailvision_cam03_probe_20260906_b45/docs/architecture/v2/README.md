# RetailVision Target Architecture v2

状态：`FROZEN_LOGICAL_ARCHITECTURE`

本目录基于当前真实代码与 ADR 0001-0020，描述 Phase 2A、Phase 2B、
Phase 2B Step 4 和 Phase 2C 完成后的逻辑模块。它不授权物理目录迁移、Schema
拆分、Production Gate 变更或 Phase 2D 实现。

## 阅读顺序

1. [PROJECT_MODULE_ARCHITECTURE_V2.md](PROJECT_MODULE_ARCHITECTURE_V2.md)：一级模块、职责和总体调用图。
2. [MODULE_DEPENDENCY_RULES_V2.md](MODULE_DEPENDENCY_RULES_V2.md)：允许依赖、禁止依赖和跨能力 Port。
3. [FACT_OWNERSHIP_V2.md](FACT_OWNERSHIP_V2.md)：事实的创建、更新和 Source of Truth。
4. [PHASE_MODULE_MAPPING.md](PHASE_MODULE_MAPPING.md)：开发 Phase 到长期模块的映射。
5. [MODULE_MAPPING_V2.md](MODULE_MAPPING_V2.md)：当前文件到目标模块的逐文件分类。
6. [TEST_ARCHITECTURE_V2.md](TEST_ARCHITECTURE_V2.md)：按模块和测试类型组织验证。
7. [MODULE_MIGRATION_PLAN_V2.md](MODULE_MIGRATION_PLAN_V2.md)：E2E 之后才可执行的迁移顺序。
8. [DEVELOPMENT_GUIDE_V2.md](DEVELOPMENT_GUIDE_V2.md)：新代码放置与评审规则。

## 权威顺序

发生冲突时按以下顺序解释：

1. 已接受 ADR 和 `CONTEXT.md` 中的领域语言；
2. 当前生产代码与测试证明的行为；
3. 本目录的 Architecture v2；
4. Target Architecture v1 文档和历史 handoff。

v1 中仍然有效的是 Business Capability First、事实所有权、Behavior 不创建
Arrival、API 不直接访问 ORM、Production 不依赖 Demo/Legacy 等原则。v1 中
“No Phase 2B capability is part of this design”、Phase 2B 仅为预留 seam、
`CustomerEntryVisit` 只能创建匿名 Visit，以及 `VisitActivity` 尚未启用等描述，
已被当前代码、ADR 0007-0020 和本架构 supersede。

## 当前硬约束

- Phase 2A-2C 已实现，但完整真实 E2E 仍为 `PENDING_VALIDATION`。
- Production Finalization Gate 仍关闭，未验证 transform 不得标为 verified。
- `agent/`、`pipelines/` 和 `agent.models` 保持当前物理位置。
- 不安装 SAM3、不实现 Phase 2D；只为 `behavior/merchandise` 预留逻辑位置。
- 不执行 Legacy/Demo 隔离、package rename、ORM split 或数据库迁移。

