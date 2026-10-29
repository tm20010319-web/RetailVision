# 玩偶与白色吉他：独立YOLO训练

本实验只新增独立文件，保留员工背心的训练脚本、配置和权重。
训练从 `data/models/yolo11n.pt` 开始，不从 `staff_vest.pt` 开始。

## 文件与参数

- 入口：`tools/training/train_toy_guitar.py`
- 参数：`config/toy_guitar_train.yaml`
- 数据：`data/toy_guitar_dataset_20261021/`
- 输出：`runs/toy_guitar/baseline_v1_20261021/`
- 类别：`0: toy`，`1: white_guitar`

所有相对路径均以项目根目录为基准。参数文件分为 `dataset`、
`training`、`evaluation`。再次训练时更换 `training.name`，避免覆盖现有结果。
更换标注来源或划分参数时，也要更换 `dataset.output`。

## 运行

仅检查数据并生成划分，不训练：

```powershell
& 'D:\RetailVisionDev\envs\agents-py311\python.exe' -B `
  'D:\project\RealtimeEntryPipeline-master\tools\training\train_toy_guitar.py' --dry-run
```

按新参数文件执行GPU训练与测试：

```powershell
& 'D:\RetailVisionDev\envs\agents-py311\python.exe' -B `
  'D:\project\RealtimeEntryPipeline-master\tools\training\train_toy_guitar.py' `
  --config 'D:\project\RealtimeEntryPipeline-master\config\toy_guitar_train.yaml'
```

默认训练参数：YOLO11n，640，batch 16，最多80轮，patience 20，seed 42，GPU 0。
使用本地已有权重；Ultralytics设置另存到新实验的输出目录中。

## 数据检查与划分

源标注包保持原样，只复制图片和标签。检查原图SHA-256、图片解码、
图片与标签配对、两类编号、矩形坐标、训练尺寸下的最小目标、精确重复内容。
近似重复的跨集合图片数量写入 `validation_report.json`。

按物品是否出现分组，每组内部按视频时间顺序以约70%/15%/15%划分。
因此三个集合都包含吉他尚未出现的画面，以及吉他出现后的画面。
每张源图只属于一个集合，划分记录在 `split_manifest.json`。

## 检查结果

- `weights/best.pt`、`weights/last.pt`：新模型权重。
- `results.csv`、`results.png`：训练过程。
- `test_evaluation/`：保留测试集的指标和图表。
- `test_predictions/`：模型实际输出的检测框图片。
- `test_predictions.json`：逐图片的类别、置信度和像素框。
- `training_summary.json`、`training_report.md`：结果及适用范围。
- `progress.json`：训练/评估/完成状态。

这次所有图片来自同一视频，物品主要保持固定位置，各集合仍高度相似。
保留测试集结果仅表示同一视频分段测试，不能证明换位置后的识别效果。
独立视频和重新摆放物品的验证状态保持 `NOT_VERIFIED`。
本入口不会把新权重接入现有服务或改变SAM3跟踪链路。

## 首次训练结果（2026-10-21）

已完成38轮，按patience=20提前停止，最佳轮次18，训练耗时57.28秒。
划分为训练142张、验证31张、测试29张。29张测试图的预测类别和数量
均与标注一致；11张无吉他画面没有吉他误报（confidence=0.25）。

同视频测试的整体precision=0.9974、recall=1.0000、mAP50=0.9950、
mAP50-95=0.9950。由于场景高度重复，这些数字不代表新位置识别能力。
`test_predictions_overview.jpg`展示全部29张模型实际检测图。
