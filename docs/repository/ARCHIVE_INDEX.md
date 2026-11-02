# 历史归档与恢复

当前入口是 [CURRENT_STATUS](../CURRENT_STATUS.md)，不是归档内的阶段计划。
以下是本机路径；其他 checkout 需由维护者提供对应归档副本，不能依赖历史目录启动服务。

| 归档根目录（D:/GJJ/work/retailvision_archive/ 下） | 内容与恢复依据 |
| --- | --- |
| `20260918_repo_cleanup/` | 本轮 68 份历史 Markdown；MANIFEST.md / manifest.json 记录每份原路径、归档路径、原因、引用、替代页、Git 状态、SHA256、时间 |
| `20260918_repo_cleanup/before/` | 本轮修改前全部 121 份 Markdown 原始字节；snapshot.json 记录范围内全部文件哈希，git-status-before.z 记录已有未提交状态 |
| `20260918_cleanup/` | 前一次源代码/配置/模型/数据库布局备份；before/ 与 backup_manifest.json。当前路径映射见下方链接 |
| `20260918_outputs_diagnostics/` | 33 份已归档诊断产物；相邻归档 manifest 可追溯，不含会员截图删除 |
| `20260826/` | 更早架构与 R5/P6S 验证历史，按其原目录与清单查找 |

当前仓库保留 [source_moves.json](cleanup_20260918/source_moves.json)、
[validation.json](cleanup_20260918/validation.json)、
[outputs 清单](cleanup_20260918/outputs_cleanup_manifest.json) 和
[此前空目录清单](cleanup_20260918/empty_directories_cleanup.json)。这些是证据，不能覆盖业务规则。

## 找回历史文档

在本轮 manifest.json 中用 original 精确检索，archived 是相对于本轮归档根的路径。
SHA256 同时适用于原文件与归档文件。保留原 bytes；不是从 Git HEAD 重建，因此也包含未提交资料。
外部 `restore_documents.ps1` 默认只显示恢复计划；使用 `-Apply` 才复制回原路径，
若目标已存在或哈希不符则终止。它不覆盖当前文档、数据库或别人的未提交变更。
要恢复被编辑的活动文档，从 before/ 选择文件并逐项比较，不整体覆盖工作区。

## 历史引用的解释

旧报告 JSON 内 artifact_sha256 的旧文档路径和哈希保持不变，按 manifest 映射至归档原文。
被精简的 MCP、cam03、v2/v3 指南原文在 before/；不能拿现在的文档哈希冒充旧验收时的文档。
归档中的原相对链接可能跨原目录；使用 original 映射在 before/ 中重建阅读上下文，不改写证据。

`scripts/audit_cam03_identity_readiness.py` 是针对固定九月六日 Run 的历史诊断入口，
仍可选地查找两个已归档的 worklog/index 路径。该列表受 `p.is_file()` 保护，
缺失只意味着新诊断不会重复收集这两份历史笔记，不影响业务运行或测试输入。
本轮按源代码只审计的边界保留该脚本；历史内容可从 manifest 找回，不能替换为当前文档伪称同一证据。

以后当前状态写 CURRENT_STATUS、决定写 ADR、长期规则更新对应指南；阶段过程与一次性报告
默认写外部 archive。每次归档都保留 SHA256 和恢复映射，不重新引入工作日志必读索引。

## Reports 专项归档

后续 Reports 阶段归档了 25 个目录 / 180 个运行产物，位置为本轮归档根的 `reports/<原目录名>/`。
其中一个目录原已为空；没有删除内容。与此前 Markdown 归档的 `reports/reports/` 路径区别开。
详见 [REPORTS_CLEANUP_MANIFEST](../../REPORTS_CLEANUP_MANIFEST.md)，逐文件哈希/引用/恢复映射位于
外部 `reports_cleanup_audit/manifest.json`，执行前审计位于同目录 inventory.json。
未关闭问题、实际 fixture/脚本输入和当前配置 provenance 共 15 组证据继续留在 reports，均不充当 Authority。
