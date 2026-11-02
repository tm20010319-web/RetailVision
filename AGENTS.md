# RetailVision 开发导航

RetailVision 分离行为 WHAT、身份 WHO、Visit 与 Effects；匿名行为成立不代表会员身份成立。
当前物理模块已在 2026-09-18 整理，逻辑所有权仍以 v2 和已接受 ADR 为基线。

## 开始工作

1. 读 [文档权威注册表](docs/authority/DOCUMENT_AUTHORITY_REGISTRY.md)，理解文档角色。
2. 读 [当前状态](docs/CURRENT_STATUS.md) 与 [当前模块布局](docs/architecture/CURRENT_MODULE_LAYOUT.md)。
3. 按任务读取 [CONTEXT](CONTEXT.md)、相关 [ADR](docs/adr/) 和模块指南。

不要求读取历史工作日志。状态写 CURRENT_STATUS，长期决定写 ADR，模块规则写现有合同；
一次性工作记录和验收报告在阶段结束后移入外部归档，参见 [归档索引](docs/repository/ARCHIVE_INDEX.md)。

## Documentation Authority

来源冲突时保持以下顺序（阅读顺序不是内容权威顺序）：

1. 当前生产 runtime 行为与测试。
2. 已接受 ADR。
3. CONTEXT 与 CURRENT_STATUS 中当前业务上下文。
4. 当前架构：v2 逻辑合同、CURRENT_MODULE_LAYOUT 物理位置。
5. 验证证据。
6. 历史与研究文档。

Registry 仅导航，不覆盖运行事实、决策和业务合同。v3 为 DESIGN_ONLY；设计和阶段计划不自动授权实施。
Stage、P6S、Offline 验收报告与工作日志仅证明当时结果，不是新开发指令。
文档整理不授权启用 Provider A、生产发布、修改 Offline V1、阈值或 Schema。

## Current Module Index

| 模块 | 当前代码入口 | 文档 |
| --- | --- | --- |
| Store / 组件 runtime | `run_store_runtime.py`、`pipelines/` | [布局](docs/architecture/CURRENT_MODULE_LAYOUT.md) |
| Identity / Member / Visit | `agent/identity/`、`agent/member/`、`agent/visits/` | [领域语言](CONTEXT.md)、[事实所有权](docs/architecture/v2/FACT_OWNERSHIP_V2.md) |
| Behavior / Offline / Evidence | `agent/behavior/`、`agent/offline/`、`agent/evidence/` | [Offline 运行说明](docs/runbooks/offline-analysis.md) |
| Greeting | `agent/behavior/staff_greeting_evaluation.py` | [当前合同](docs/domains/staff-greeting.md) |
| Analytics / MCP | `agent/analytics/`、`agent/mcp_server/` | [MCP 指南](docs/product_analytics_mcp_server.md) |
| API / 前端 | `run_member_api.py`、`agent/api/`、`frontend/` | [根 README](README.md) |
| 配置 / 本地资产 | `configs/`、`runtime/databases/`、`data/models/` | 各目录 README |
| 测试 / 演示 / 工具 | `test/`、`demo/`、`tools/` | 各目录 README |

## 项目规则

- 单一领域上下文：[domain.md](docs/agents/domain.md)。
- Issues / PRD 使用 GitHub Issues：[issue-tracker.md](docs/agents/issue-tracker.md)。
- 分诊使用五个默认标签：[triage-labels.md](docs/agents/triage-labels.md)。
- 兼容代码、CLI、migration、动态 adapter 不能因无静态 import 或名字旧而删除。
- 保存已有未提交工作。源代码重构、数据库变更与文档治理分别处理。
