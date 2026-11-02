# 当前物理模块布局

状态：`CURRENT_AUTHORITY / PHYSICAL_LAYOUT_ONLY`。生效日期：2026-09-18。

用户明确授权清理遗留实现并整理模块、根目录 JSON 和数据库，因此本轮执行了物理布局
迁移。本文覆盖 v2/v3 中旧文件位置和物理冻结措辞，**不覆盖**事实所有权、算法、阈值、
身份资格、业务模式、数据库 Schema 或生产发布门槛。架构设计仍参考 v2 和已接受 ADR。

## 组织规则

根 README 的目录地图是模块入口。新 Python 代码归入所属业务包，使用显式完整导入，
例如 `agent.offline.offline_behavior`、`agent.analytics.member_behavior`。
包 `__init__.py` 不组装模型、设备、数据库或业务 worker。

| 模块 | 接收/提供 | 不拥有 |
| --- | --- | --- |
| identity | Recognition Inbox、Event-Time 身份证据、同相机 Track 评估/绑定 | 商品交互事实、到访合并决策 |
| behavior | Person/Product observation、Stage8/9、Entry/Greeting 评估、行为持久化 | 通过行为猜会员身份 |
| offline | Source/PTS、历史身份投影、Run seal、资格检查和 publication | 新到访、欢迎或优惠券 |
| analytics | 有准入约束的只读查询、商品匿名统计写入与查询、MCP request adapter | 设备控制、LLM 自行写业务事实 |
| member | 会员 CRUD、设备映射、历史与统计 | Track 空间关联判断 |
| visits | Reusable/Canonical Visit、merge、activity、arrival/effects | SAM3 跟踪、商品动作识别 |
| evidence | 设备录像与截图获取/存储 | 行为和身份资格的替代判断 |
| api / mcp_server | 请求校验、协议、组合与公开结果 | 把受限身份诊断直接公开 |
| platform | 捕获/相机适配、租约、显示基础设施 | 业务规则 |
| runtime / pipelines | Provider 窗口、应用装配与生命周期 | 重复实现业务状态机 |

## 保留的兼容实现

`agent/legacy/` 和 `pipelines/legacy/` 仍有合法调用者。旧运行模式未在清理中被偷偷
停用，new/legacy 互斥、Store lease 和 fail-fast 仍保留。`pipelines` 的旧公开工厂改为
按需导入，不再在导入离线 pipeline 时加载整个旧画像/检测链。

`agent/profile/` 与 `demo/` 为可选画像/演示。`demo/greeting/` 是宽松演示规则，
`agent/behavior/staff_greeting_evaluation.py` 是正式评估，不可混用。

Stage10 的共享 provider 合同与窗口控制保留于 `agent/runtime/`；展示素材/验收工具
移至 `tools/acceptance/`。现有 Golden analyzer 继续在
`scripts/run_offline_complete_loop_golden.py`，当前 cam03 仍依赖它。

`agent/models.py`、`agent/db.py`、`agent/schema_evolution/` 保留原公共位置，避免模块
整理变成 ORM 拆分或数据库迁移。`agent.paths` 仅拥有文件位置和受限本地兼容。

## 配置、数据与测试

- 根目录不存放相机 JSON、SQLite 或模型；分别使用 `configs/`、`runtime/databases/`、
  `data/models/`。环境变量继续可以覆盖默认值。
- 初始化/Mask 与特定视频绑定，不按相似文件名自动合并相机配置。
- 已存在的 Run 证据、哈希、历史报告正文不重写；老路径由迁移清单解释。
- 测试源码公开可见；临时数据库、截图、缓存和输出不能混入代码提交。
- `test/` 保留现有测试文件位置，以免相对 fixture 和跨测试辅助函数同时发生变化。
  测试职责分组由 test/README 提供，新的隔离检查见 test_repository_layout.py。

## 审查与兼容

文件迁移映射和退休列表位于 `../repository/cleanup_20260918/source_moves.json`。
旧 `agent.<flat_module>` 导入已在本仓库调用点统一更新；外部 Python 调用需按该表迁移。
MCP 启动模块 `agent.mcp_server.server` 和四个根 runtime/API 入口保留。

生产依赖应逐步减少 legacy/profile 耦合；本轮没有声称所有历史运行路径已删除。
未来 Schema、算法、设备策略或生产 enablement 应独立评审，不混入目录整理。
