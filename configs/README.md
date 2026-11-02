# 配置目录

- `cam02/`：cam02 的行为相机、商品跟踪、人物商品关联、交互状态机配置。
- `cam03/`：cam03 商品初始化配置、识别相机配置和能力 Profile。
- `entrance/`：入口相机。
- `examples/`：示例配置。
- `training/`：训练配置。

JSON 文件迁移不改变阈值、相机身份、source_video 或 Profile 哈希。
不要把不同视频的初始化/Mask 确认配置视为重复版本随意合并。

从仓库根目录启动，或给 CLI/环境变量提供绝对路径。例如：

```powershell
$env:BEHAVIOR_CONFIG_PATH = 'D:/GJJ/work/code/configs/cam03/behavior_cameras.cam03.identity.json'
$env:RETAILVISION_CAMERA_CONFIG_PATH = $env:BEHAVIOR_CONFIG_PATH
$env:RETAILVISION_CAPABILITY_PROFILE_PATH = 'D:/GJJ/work/code/configs/cam03/p6s_cam03_offline_identity.profile.json'
```
