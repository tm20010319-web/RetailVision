# RetailVision

离线门店录像分析与匿名商品互动识别。当前源码包含 VLX 拿起、持有、放下计数/计时，以及 Windows 固定物品组合运行入口。

## 运行 VLX 录像

1. 准备已有 VLX、SAM3.1、Python 3.11 与 FFmpeg 环境，参考 [环境与启动材料](handoff/) 及 [详细运行说明](docs/VLX录像双击运行说明.md)。
2. 配置 config/vlx_video_launcher.json 和 config/vlx_interaction_refined.yaml 中的本机环境、权重位置，并检查关联 VLX 配置。
3. 双击 start_vlx_video.bat，输入录像路径并选择三件玩偶、白色电吉他或原有六件物品。
4. 结果包自动保存到 deliveries/<录像名称>/，每次运行保留独立版本。结果仍需人工复核，未写入数据库。

## 验证与限制

本地当前功能验证：123 项相关测试通过，3 秒真实三玩偶片段已完成识别、跟踪、视频渲染、导出和打包。公开副本的验证见 PUBLIC_VALIDATION.json。
短片流程通过不代表长录像零漏检；遮挡推定与未完成会话保留标记。同类别多个实例不受这些固定组合支持。

## 源码与依赖

- agent/、scripts/、pipelines/：现有业务模块与入口。
- config/、configs/、handoff/：配置、模板和安装材料。
- test/、demo-frontend/：后端测试及原前端源码。
- dependencies/：此前交付中的外部组件源码，各自遵循原许可证。
- [公开快照说明](PUBLIC_SOURCE_NOTES.md)：排除项、脱敏处理与历史边界。

不包含模型、虚拟环境、真实密钥、数据库、运行录像和日志。

原始前端的 3D 大象资源仅有 Git LFS 指针，模型实体未随原源码提供；详见公开快照说明，不影响 VLX 录像处理。
