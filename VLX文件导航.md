# VLX 文件导航

更新日期：2026-10-29。本页导航 VLX 的固定入口、测试输出与交付材料。

新录像请双击项目根目录的 [start_vlx_video.bat](start_vlx_video.bat)，按提示选择录像和物品组合。详见 [运行说明](docs/VLX录像双击运行说明.md)。

## 先从这里找

| 要找的东西 | 位置 | 说明 |
| --- | --- | --- |
| 发给同事的结果压缩包 | [交付目录](deliveries/README.md) | 按录像名称分文件夹，ZIP 与 SHA256 放在一起 |
| 三件玩偶的最新识别视频 | [v5 视频](runtime/vlx-interaction-video/test_20261028_175611_v5/annotated.mp4) | 4 次拿起、4 次放下；白色玩偶含 11 秒遮挡推定 |
| 三件玩偶的详细说明 | [修复说明](runtime/vlx-interaction-video/test_20261028_175611_v5/修复说明.md) | 用户、物品、次数、持有时间和结果限制 |
| 三件玩偶的导出事实 | [interaction_facts.json](runtime/vlx-interaction-video/test_20261028_175611_v5_export_20261029/interaction_facts.json) | 12 条，未入库 |
| 三件玩偶的历史试验 | [版本说明](runtime/vlx-interaction-video/README.md) | v3、v4、v5 用途有区别 |
| 家庭监控和眼睛按摩仪测试 | [测试说明](runtime/vlx-home-items/README.md) | 动作识别验收未通过，不能把自动 0 次当成真实无动作 |
| 七分钟全商品候选测试 | [测试说明](runtime/video-analysis/20261028_9f61f800_all_goods/README.md) | 全量动作识别验收未通过 |
| 已归档的 VLX 日志、复核截图 | [归档说明](runtime/vlx-archive/20261029-output-tidy/README.md) | 40 个文件，全部保留，附新旧路径与校验记录 |

## 本次整理范围

- 归档家庭物品测试根目录的日志、SAM 复核图和源视频复核截图，以及三玩偶修复诊断目录的两个日志。
- 运行目录内的结果、manifest、input、采样帧、SAM 几何记录保留原路径；这些材料互相引用，继续支持历史回放。
- `pytest_20261028_run1` 内有依赖原路径的符号链接，保持原位。它是历史测试临时目录，不是实际录像识别结果。
- 原诊断脚本保持原位。再次运行可能重新生成同名日志或截图；新生成文件不属于此次历史归档，勿直接覆盖归档。
- 当前目录以外的 `D:/project/RetailVisionDev` 仍被 VLX 配置引用；本次未移动其运行记录、模型或环境。
- 其他业务模块、入口人数统计目录及数据库不在本次整理范围内。

本次没有删除文件，也未修改源码、配置或交付包。364 个受保护文件（agent、scripts、config、test 和 deliveries 下的非缓存文件）整理前后哈希一致。此次验证是文件与路径完整性检查，没有重新运行模型推理。
