# 仓库文档治理完成报告

日期：2026-09-18。执行范围：文档治理、历史资料归档、遗留代码审计。
本轮未修改业务代码、配置、数据库、模型、训练数据、历史证据 JSON 或 ADR；已有未提交工作保留。

## 1. Cleanup Summary

- 121 份原始 Markdown 完成分类；68 份移到仓库外并逐份核对 SHA256。
- 原文保留 53 份，新增 7 份计划/报告/当前合同与索引，最终 60 份 Markdown。
- 20 份 ADR 原样保留；50 份工作日志及其索引退出当前必读路径。
- 遗留候选 56 个：45 KEEP、11 REVIEW、0 REMOVE_LATER；未删除源代码。

完整分类见 [治理计划](DOC_GOVERNANCE_PLAN.md)。范围排除 Git 元数据、其他工作树、依赖和环境。

## 2. Canonical Documentation Set

新会话先读 [AGENTS](AGENTS.md)、[当前状态](docs/CURRENT_STATUS.md)、
[当前布局](docs/architecture/CURRENT_MODULE_LAYOUT.md)，按任务补读 [CONTEXT](CONTEXT.md) 和 [ADR](docs/adr/)。
[文档入口](docs/README.md) 路由到 v2 合同、MCP、cam03、Offline 和 Greeting；没有机械新增重复总览文档。

## 3. Archived Documents

| 执行分类 | 数量 |
| --- | ---: |
| HISTORICAL_WORKLOG（含索引） | 51 |
| HISTORICAL_HANDOFF | 1 |
| GENERATED_REPORT | 10 |
| DUPLICATED_SUPERSEDED | 6 |
| 合计 | 68 |

外部目录：`D:/GJJ/work/retailvision_archive/20260918_repo_cleanup/`。
MANIFEST.md / manifest.json 记录原路径、归档路径、分类、原因、引用、替代页、Git 状态、SHA256 和时间。
before/ 保存执行前全部 121 份 Markdown；snapshot.json 与 git-status-before.z 保存文件/Git 状态。
restore_documents.ps1 默认只预览；68 个恢复路径已通过哈希与不覆盖检查，未实际恢复。
入口与恢复规则见 [归档索引](docs/repository/ARCHIVE_INDEX.md)。

## 4. Merged / Superseded Documents

- 交接与最新记录的能力、资格、基线和待办汇入 CURRENT_STATUS；移除“商品分析尚未实现”的旧指令。
- 提取 [Greeting 合同](docs/domains/staff-greeting.md)；原风险登记仍有长期价值，保留并修复代码链接。
- v2 旧 Phase/文件映射和迁移计划归档，物理位置由 CURRENT_MODULE_LAYOUT 与 source_moves.json 负责。
- MCP 指南去除阶段评分流水，保留工具/运行合同，记录 R5-04.2 仍验收阻断。
- cam03 指南消除冲突的启动、阈值和校准叙事，保留 source-bound 初始化与只读身份诊断。
- 新增 [Offline runbook](docs/runbooks/offline-analysis.md)，复用现有模块映射。

## 5. Current Architecture Authority

保持 runtime/tests → accepted ADR → CONTEXT/当前状态 → 当前架构 → 证据 → 历史。
Registry 仅导航；v2 定义逻辑所有权，CURRENT_MODULE_LAYOUT 定义实际位置；v3 为 DESIGN_ONLY。
v3 更新已有文件路径，去掉退休 shim 的当前使用指令；未来子目录不宣称已经实施。
Event 804/B2.8、Provider A、Offline、Greeting、生产 gate、Schema 和阈值均未改变。

## 6. AGENTS.md Changes

以当前状态和模块索引替代 WORKLOG_INDEX 必读要求，保留 Issue/PRD/domain 规则。
当前状态就地更新，长期决定写 ADR，一次性过程与报告完成后归外部 archive。

## 7. Broken References Fixed

- 修复 Greeting 风险文档中已搬迁的 staff_greeting_offline.py 链接。
- 更新 AGENTS、根/docs README、Registry、测试指南对已归档文档的导航。
- 根据 source_moves.json 修复活动架构中的旧路径，区分 v3 target 与当前路径。
- 扫描本地 Markdown 链接、脚本/代码、配置、测试及证据文本；结果见外部 verification.json。
- 原 artifact_sha256.json 的历史路径/哈希不改写，通过 manifest 找回原文件。
- audit_cam03_identity_readiness.py 的两个可选旧笔记路径受 p.is_file() 保护，缺失不阻断运行；
  新诊断不再自动收集这两份旧笔记，语义已在归档索引说明。按本轮只审计边界保留脚本。
- 计划中的旧路径是执行前清单，不是活动导航；归档原文不为追求链接通过而修改字节。

## 8. Remaining Review Items

- 既有 Python 8 个失败与前端 chunk 警告未修复；此前 1472 passed 不是本轮重跑。
- ADR 0007–0018 缺显式状态，未推断 accepted/superseded。ADR 0005 的窄例外不等于整份失效。
- v3 接口深化、legacy 退休、Greeting 多视频基线/标定/性能/只读接入分别评审。
- 未检查外部 Java Agent 当前部署；本机归档需单独提供给其他开发机器，不应成为运行依赖。
- 自动审批检查拒绝候选空目录清除命令（blocked by policy），没有执行删除。
  复核仅 docs/handoff、docs/reports、skill 是 3 个空目录；docs/worklogs 仍有两份 DPI-01C3 证据 JSON，必须保留。

## 9. Legacy Code Candidates

[候选表](LEGACY_CODE_CANDIDATES.md) 逐项记录 purpose、references、runtime/test reachability、
replacement、confidence 与 recommended_action。340 个 Python 文件 AST 无解析错误。
legacy pipelines、Stage10 runtime、Golden analyzer、device smoke helper 仍有消费者；
无入链 CLI、migration、动态 adapter 和手工设备脚本不能直接删除。本轮未批量运行硬件脚本。

## 10. Repository Tree After Cleanup

```text
AGENTS.md / README.md / CONTEXT.md
DOC_GOVERNANCE_PLAN.md / LEGACY_CODE_CANDIDATES.md / REPOSITORY_GOVERNANCE_REPORT.md
docs/
  CURRENT_STATUS.md / README.md
  authority/                  导航
  architecture/               当前布局、v2 合同、v3 设计
  adr/                        20 份原始决策
  domains/staff-greeting.md
  runbooks/offline-analysis.md
  agents/                     Issue / triage / domain
  repository/                 归档索引、风险、时间债务、此前清理 JSON
  worklogs/                   仅两份原始证据 JSON
agent/ / pipelines/           本轮原样
configs/ / frontend/ / test/ / tools/ / demo/
runtime/databases/ / data/models/ / outputs/ / reports/
```

验证采用文件哈希、恢复预览、链接/引用扫描、AST 和文档 whitespace 检查。
未启动服务、设备或模型，未迁移数据库，未重跑业务测试，未暂存或提交 Git。

最终核验：109 个本地 Markdown 链接全部有效；68 份归档哈希一致；
2,546 份原有非 Markdown 文件全部字节不变，20 份 ADR 不变；无非预期丢失。
原文档修改 17 份，新增 7 份。历史路径剩余引用仅在执行前计划、5 个证据 JSON 和上述历史诊断脚本，
均已分类登记。3 个空目录仍保留，工作日志目录内的 2 份证据 JSON 原样保留。
