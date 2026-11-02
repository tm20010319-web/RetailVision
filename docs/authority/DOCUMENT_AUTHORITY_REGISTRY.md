# Document Authority Registry

状态：CURRENT_AUTHORITY / ROUTING_ONLY。优先阅读表示导航优先，不表示业务权威高于代码或 ADR。

## 冲突处理

1. 当前生产 runtime 行为与测试。
2. [已接受 ADR](../adr/)。
3. [CONTEXT](../../CONTEXT.md) 与 [当前状态](../CURRENT_STATUS.md)。
4. [v2 逻辑合同](../architecture/v2/README.md) 和 [当前物理布局](../architecture/CURRENT_MODULE_LAYOUT.md)。
5. 验证证据。
6. 历史 / 研究记录。

Registry 仅导航，不定义数据库、API/MCP 或业务合同。未接受的未来设计不能覆盖上面的当前来源。
ADR 0007–0018 没有显式 status 元数据；保留原文，不推断 accepted 或 superseded。

## 分类与路由

| 分类 | 路由 / 用法 |
| --- | --- |
| CANONICAL | [AGENTS](../../AGENTS.md)、CONTEXT、CURRENT_STATUS、当前布局与 v2 核心合同 |
| ADR | 决策历史长期保留；只有明确后继决定才能标 superseded |
| ACTIVE_REFERENCE | [模块指南索引](../README.md)；v3 仅 DESIGN_ONLY；Greeting 风险是有界风险参考 |
| HISTORICAL_WORKLOG / HISTORICAL_HANDOFF | 外部归档；开发过程，不是当前合同 |
| GENERATED_REPORT | 一次验证记录；长期结论吸收到当前合同后归档，证据不改写 |
| DUPLICATED_SUPERSEDED | 已过时的阶段/文件映射，按 [归档索引](../repository/ARCHIVE_INDEX.md) 找原文 |

当前物理位置不能从老阶段号推断。v2 保留逻辑边界，v3 保留未来设计；实际能力与资格由
CURRENT_STATUS 和运行代码/测试核对。`CURRENT_AUTHORITY` 只在文档自身责任内有效。

## 维护

当前状态更新 CURRENT_STATUS；长期接口与操作说明就地更新；不可逆决策补 ADR。
完成的工作日志与一次性报告放外部 archive，不新增长期 WORKLOG_INDEX。
归档必须保存原路径、替代文档、引用、Git 状态、SHA256 和恢复映射。原始证据中的路径和哈希保持不变。
若活动文档仍指示与当前事实冲突的操作，标 CONFLICTING 并修正；未充分证明的疑点标 REVIEW_REQUIRED。
