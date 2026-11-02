# RetailVision Architecture v2

状态：FROZEN_LOGICAL_ARCHITECTURE。这里定义长期能力边界，不是物理迁移任务列表。
2026-09-18 的实际文件位置见 [当前布局](../CURRENT_MODULE_LAYOUT.md)，实现/资格见
[当前状态](../../CURRENT_STATUS.md)。Offline、SAM3、Analytics、MCP 已实现，不能按早期 Phase 2D 预留措辞判断缺失。

## 按需阅读

1. [模块架构](PROJECT_MODULE_ARCHITECTURE_V2.md)：职责和逻辑调用图。
2. [依赖规则](MODULE_DEPENDENCY_RULES_V2.md)：Port 与禁止边。
3. [事实所有权](FACT_OWNERSHIP_V2.md)：事实、projection 和 provenance。
4. [开发指南](DEVELOPMENT_GUIDE_V2.md)：新代码与接口评审。
5. [测试架构](TEST_ARCHITECTURE_V2.md)：验证类型与范围。

冲突顺序与 [Registry](../../authority/DOCUMENT_AUTHORITY_REGISTRY.md) 一致：runtime/tests、
accepted ADR、CONTEXT/当前状态、当前架构、证据、历史。逻辑树不是承诺每个子目录已经存在。

早期 Phase 映射、旧逐文件清单与迁移计划已进入 [外部归档](../../repository/ARCHIVE_INDEX.md)。
它们不再规定当前放置位置。已完成的目录整理不代表完成真实生产 E2E 或允许放开 finalization/publication gate。
Schema、阈值、identity policy 和生产资格的变更仍是独立任务。
