# VLX 录像固定入口

适用范围：Windows、本机已经部署的 VLX 与 SAM 环境、已有固定物品组合。每次更换录像只需输入路径并选择组合；计数规则沿用当前版本。

## 日常使用

1. 在项目根目录双击 `start_vlx_video.bat`。
2. 粘贴录像完整路径，按回车。路径可以包含中文、空格，也支持资源管理器“复制文件地址”得到的带双引号路径。
3. 输入物品组合编号：

| 编号 | 组合 |
| --- | --- |
| 1 | 橙色卡通玩偶、白色蛋形玩偶、粉色毛绒玩偶 |
| 2 | 白色电吉他 |
| 3 | 以上三件玩偶、黄色机器狗、白色电吉他、小蜜蜂玩偶 |

4. 等待运行。窗口显示阶段及日志位置，每 20 秒提示仍在运行。模型加载也需要时间。
5. 出现“处理和打包完成”后，从窗口给出的路径或 `deliveries/README.md` 获取 ZIP。结束窗口保留，按任意键关闭。

输入 `q` 可在选择阶段退出；处理过程中使用 Ctrl+C 取消，保留当前日志和中间记录。请等待取消完成后关闭窗口。
同一项目的固定入口只允许一个识别任务同时运行，避免重复双击占用显存。此限制不控制其他工具启动的 GPU 作业，请避免同时运行其他模型任务。

## 文件位置

- 本次任务与阶段日志：`runtime/vlx-video-jobs/<录像名称>/<UTC时间_随机编号>/`。
- 标注视频与自动结果：该任务下的 `result/`。
- 原始 VLX／姿态识别记录：`raw_vlx_pose/`。
- 标准化互动事实：`export/interaction_facts.json`。
- 压缩包和校验文件：`deliveries/<录像名称>/RetailVision-<组合>-<运行编号>.zip` 及 `.zip.sha256`。
- 同一录像重复运行会创建新编号，旧结果不会覆盖。

ZIP 根目录包含 `annotated.mp4`、`result.json`、`events.jsonl`、`manifest.json`、`interaction_facts.json` 和 `.jsonl` 版本，保留采样帧、跟踪几何、原始识别和 SHA256 清单。跟踪窗口内重复的帧硬链接不重复打包，完整采样帧保存在 `frames/`。日志保存在任务目录。
包内 `job.json` 是开始打包时的处理完成快照；任务目录的 `job.json` 在最终完成后还会记录交付路径。

## 配置与启动依赖

日常更换录像不用修改配置。换机器或安装位置变化时，在 `config/vlx_video_launcher.json` 修改 Python、FFmpeg 和细化配置位置。
SAM Python、仓库、权重以及行为配置引用仍由 `config/vlx_interaction_refined.yaml` 管理；本入口使用 launcher 配置中的 `vlx_python` 作为统一 VLX 解释器。
采样和行为参数沿用 `config/vlx_interaction_video.yaml`，VLX 权重与外部仓库沿用它引用的 `config/vlx_seek_trial.yaml`。
没有新增第三方依赖；使用已有两个 Python 环境、PyYAML、模型依赖和 FFmpeg。入口不会下载或安装模型。

可先在项目根目录执行环境检查（不加载权重）：

```powershell
& 'D:/project/RetailVisionDev/envs/vlx-interaction-py311/Scripts/python.exe' -B scripts/run_vlx_video_job.py --check
```

命令行等价调用：

```powershell
& 'D:/project/RetailVisionDev/envs/vlx-interaction-py311/Scripts/python.exe' -B scripts/run_vlx_video_job.py --video 'D:/surveillance_video_raw/2026_10_28_17_56_11.mp4' --preset three_toys
```

支持的 preset 为 `three_toys`、`guitar`、`six_items`。菜单暂不加入任意新物品，也不加入此前动作验收失败的家庭监控／按摩仪实验。

## 识别边界与失败处理

组合按已有每类一个固定物品实例使用，不支持同类别多个实例的独立计数。请选择录像实际包含的组合。SAM 玩偶跟踪需要在前两秒建立明确实例，并在后续窗口重叠区重新关联；缺少目标或长期遮挡可能使该阶段停止。
窗口大小默认 150，保持原流程默认值。配置中的 `tracking_chunk_size` 可调整，但增大值会增加显存占用，不能保证解决遮挡问题。

环境、解码、识别、跟踪、导出或校验失败都会显示阶段与日志，保留中间记录，不会把失败材料标为完成的交付。修正环境或输入后重新运行即可；旧失败目录仍可复核。

自动处理完成不代表识别准确性验收通过。结果包统一带 `requires_visual_review` 标记；0 次不能证明没有动作，遮挡推定和未完成会话保留原始标记。仍然只记录匿名用户、原视频相对秒数，没有新增数据库写入，也不推测实际摄像头或录像开始时间。

本入口解决的是每段录像的启动、流程与交付一致性。若发现通用程序缺陷才需要修源码；它不承诺所有录像无漏检，也不根据预期次数自动调规则。
