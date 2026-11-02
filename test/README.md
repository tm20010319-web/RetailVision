# 自动化测试

从仓库根目录运行，使用已安装依赖的 agents-py311 Python：

```powershell
& 'C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe' -B -m pytest -q -p no:cacheprovider
& 'C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe' -B -m pytest demo/camera_profile/tests -q -p no:cacheprovider
npm --prefix frontend test
```

pytest.ini 将发现范围限定为 test/，设置辅助模块路径和 importlib 收集，避免项目文件夹
名 code 与 Python 标准库冲突。根目录的旧包 __init__.py 已退役。手工设备工具在
`tools/manual/`，不可批量执行。

| 范围 | 测试文件前缀/代表 |
| --- | --- |
| 识别/到访/绑定 | device_*, recognition_*, phase2*, reusable_visit_*, visit_*, track_* |
| 行为/商品/人员 | behavior_*, person_*, product_*, sam31_* |
| 离线资格和发布 | offline_*, b28_*, cam03_* |
| 会员与 MCP | member_*, product_analytics_*, product_interaction_mcp |
| Greeting | staff_greeting_*, realtime_greeting_*, demo_greeting_* |
| 兼容旧流程 | realtime_entry_*, entry_event_coordinator, member_entry_service, profile_* |
| 目录迁移 | repository_layout：数据库指向保护、离线入口导入隔离 |

所有现存自动化测试源码均不再被 .gitignore 隐藏。原 19 个隐藏文件中，两个手工
摄像脚本分别移动到 tools/manual/ 或随旧 Agent 归档；一个依赖已删除 initialize_schema
接口、无法收集的历史测试归档到外部恢复目录。其他测试保留并显式可见。

2026-09-18 原始基线：1462 passed、8 failed、4 skipped、1 collection error，另有
276 subtests passed。未用恢复废弃 API、改算法或放宽断言来粉饰这个结果。最新迁移
验证见 [当前状态](../docs/CURRENT_STATUS.md)。完整运行仍应报告既有失败。

特定测试使用临时 SQLite、FFmpeg 或 runtime stubs；不能再沿用旧 README 的“所有测试
不碰数据库”描述。新 checkout 应安装 test/runtime 依赖并提供需要的 FFmpeg。
