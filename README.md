# RetailVision

门店视频行为分析与会员识别。系统分别处理 **WHAT（行为）** 和 **WHO（身份）**：
匿名商品互动可以被分析，会员身份只能来自满足现有资格规则的确认结果。

## 目录地图

| 目录 | 责任 |
| --- | --- |
| `agent/identity/` | 设备识别接入、历史证据、Track 关联与绑定 |
| `agent/behavior/` | 人员/商品跟踪、交互和 Greeting 规则 |
| `agent/offline/` | 视频来源、时间线、离线身份、Run 产物与发布门槛 |
| `agent/analytics/` | 匿名商品统计、确认会员行为查询、MCP 工具适配 |
| `agent/member/` | 会员管理、设备注册、统计与历史查询 |
| `agent/visits/` | 到访、复用/合并、活动与 Arrival Effects |
| `agent/evidence/` | 录像与截图存储 |
| `agent/api/` | HTTP API 和运营查询 |
| `agent/platform/` | 摄像头适配、采集、运行租约和文字渲染 |
| `agent/runtime/` | SAM3/离线复用的窗口控制、Provider 合同与展示实现 |
| `agent/profile/` | 可选人物画像工作流与 LLM 适配 |
| `agent/legacy/` | 尚被兼容流程使用的旧识别协调、会员入口与检测器 |
| `agent/mcp_server/` | MCP 协议服务器，公开启动路径保持不变 |
| `agent/models.py`, `agent/db.py`, `agent/schema_evolution/` | 集中式持久化合同与 schema 演进 |
| `pipelines/` | 当前 Store、Device、Behavior 和 Offline 装配 |
| `pipelines/legacy/` | 明确隔离的旧进店/画像/历史视频装配 |
| `frontend/` | 运营台和 `/customer-display` 顾客屏 |
| `configs/` | 按相机、示例和训练分类的配置 |
| `scripts/` | 当前运行、诊断、资格验证入口 |
| `tools/` | `replay/` 回放、`acceptance/` 验收、`manual/` 手工设备工具、训练与 Harness |
| `demo/` | `greeting/`、`camera_profile/`、`product_analytics_agent/` 与旧 CLI |
| `test/` | 自动化测试；源码不再被 .gitignore 隐藏 |
| `runtime/databases/` | 本地 SQLite 数据，不提交 Git |
| `data/models/` | 本地模型权重，不提交 Git |
| `reports/`, `outputs/` | Reports 保留必要运行证据，其余已外部归档；outputs 按引用与留存规则管理 |
| `docs/` | 当前状态、领域合同、ADR、架构和运行说明 |

详细规则见 [当前模块布局](docs/architecture/CURRENT_MODULE_LAYOUT.md)。2026-09-18 整理
保留业务算法、阈值、Schema 和发布资格；文件名中的 Stage 编号仍可追溯历史实现。

## 启动

从仓库根目录运行。Windows 已有视觉环境为 `agents-py311`，不要用基础 Conda 环境
替代。模型、设备连接和业务模式需要显式配置，整理目录不会自动开启生产发布。

```powershell
$rvPython = 'C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe'
$env:DATABASE_URL = 'sqlite:///D:/GJJ/work/code/runtime/databases/demo_flow.db'

# 按实际任务选择一个入口；启动前配置对应模式和相机。
& $rvPython run_member_api.py
& $rvPython run_store_runtime.py
& $rvPython -m agent.mcp_server.server
```

cam03 受控 Push 接入继续使用 `scripts/start_cam03_push_receiver.ps1`。相机 JSON 和
能力 Profile 的新路径见 [configs/README.md](configs/README.md)。示例命令不代表
已经启动服务，也不替代 scope/identity/production gate。

```powershell
# 前端（进入目录后执行）
cd frontend
npm ci
npm run dev

# 可选演示，从仓库根目录执行
python -m demo.greeting
python -m demo.camera_profile
python -m demo.legacy_cli --help
```

MCP 默认仍关闭。`RETAILVISION_DATABASE_URL` 和 `RETAILVISION_MEMBER_DATABASE_URL`
可分别指定匿名商品与会员行为数据源。数据库原文件已逐一备份与校验；使用原根目录
URL 的 Settings/MCP 调用有受限兼容，直接 sqlite3/外部 SQL 脚本需要更新路径。

## 验证

```powershell
& $rvPython -B -m pytest -q -p no:cacheprovider
& $rvPython -B -m pytest demo/camera_profile/tests -q -p no:cacheprovider
npm --prefix frontend test
npm --prefix frontend run build
```

全库原有失败未通过修改算法或放宽断言来隐藏。基线与当前检查结果见
[当前状态与基线](docs/CURRENT_STATUS.md)，测试分组见
[test/README.md](test/README.md)。`tools/manual/` 中部分脚本会操作真实设备，不能作为
自动化测试收集或批量执行。

## 恢复和历史

- 文件映射：[source_moves.json](docs/repository/cleanup_20260918/source_moves.json)。
- 本地原文件备份：`D:/GJJ/work/retailvision_archive/20260918_cleanup/before/`；校验清单
  位于同级 `backup_manifest.json`。数据库和本地测试也已保存。
- 已退役：旧 LangGraph 视觉 Agent/本地人脸/推荐闭环、模拟 analytics、旧路由、两个
  Harness 转发壳和旧前端副本。旧 Detector 手工入口及依赖废弃 API 的历史测试一并归档。
  仍有消费者的设备 smoke 工具保留于 tools/manual/，并改用当前 Recognition Intake 的事件键函数。
- 其他 Git 工作树包含独立工作，未由本轮整理移除。

文档阅读顺序与历史恢复见 [文档入口](docs/README.md) 和 [归档索引](docs/repository/ARCHIVE_INDEX.md)。

Reports 历史产物的保留与归档见 [专项清单](REPORTS_CLEANUP_MANIFEST.md)。

## VLX 固定组合录像入口

现有 VLX 功能继续通过 start_vlx_video.bat 运行。环境安装参考 [handoff/](handoff/)，配置 config/vlx_video_launcher.json 后，按 [录像运行说明](docs/VLX录像双击运行说明.md) 操作。

本仓库只发布源码、测试、配置模板和必要文档；模型、录像、数据库与本地凭据需要另行准备。公开处理和验证边界见 [PUBLIC_SOURCE_NOTES.md](PUBLIC_SOURCE_NOTES.md)。
