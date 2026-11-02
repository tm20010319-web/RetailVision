# Offline 分析运行边界

从仓库根目录使用已有环境。先准备明确的 source、PTS、历史注册/时钟、相机配置和输出目录。
CLI 加载显式的 `module:callable` adapter，静态无入链不代表 adapter 废弃。

```powershell
python scripts/run_offline_behavior_analysis.py --plan <plan.json> --behavior-analyzer <module:callable> --artifact-root <new-output-directory>
```

启用匿名商品 projection 时还必须显式提供 `--db-url`，并满足现有 source、scope、时间和 sealed Run 条件。
projection 开关、MCP 开关、正式 BehaviorEvent publication gate 是不同边界，不能互相替代。
具体冻结合同和 qualification 看 [当前状态](../CURRENT_STATUS.md)，模块入口看
[Offline V1 模块映射](../offline_v1_module_mapping.md)。

cam03 使用 [采集与身份指南](../cam03_new_sample_recording.md)；共享 GoldenMp4Analyzer 仍在
`scripts/run_offline_complete_loop_golden.py`，不能按文件名当测试垃圾移除。
正式 Staff Greeting 使用 [Greeting 合同](../domains/staff-greeting.md) 指定的独立入口。

数据库 migration、Provider A production、发布 gate、硬件运行都需要对应任务范围；
阅读本指南或完成目录治理不构成启用授权。不要对诊断产物补写正式事实来制造通过结果。

## Demo 渲染与固定输入

`scripts/render_cam03_member_demo.py` 读取固定 Run 自身的 tracks、Stage9 events、source timeline 和
formal association，使用源视频重新绘制，不重新运行行为分析。变帧率源解码保持每个原帧一次输出
（FFmpeg passthrough），再按输出时间 k/25 选择原始 PTS 不晚于该时间的最后一帧。
不能先让 FFmpeg 自动补帧再按原始帧序号取样，否则时间映射会错位。展示帧不是分析采样，不改变事实时间。
历史修复验证记录了 4,859 个输出映射检查通过；这不是本轮重跑，也不证明 OSD/设备时钟同步。

该脚本的默认 Run/identity 输入保留原位置；历史输出视频和验证包已归档。
再次生成时用 `--output-dir` 选择新的空目录，不能覆盖旧证据。恢复旧产物按仓库的 Reports 清单映射查找。
