# 黄色小蜜蜂玩偶、白色吉他：多姿态第二版

独立配置：`config/toy_guitar_train_v2.yaml`。
独立入口：`tools/training/train_toy_guitar_v2.py`。
原员工马甲配置、第一版训练配置和权重、当前摄像头预览配置均保留。

## 数据和标注

新视频：`D:\surveillance_video_raw\2026_10_21_22_12_09.mp4`，约 362 秒。
按媒体时间每 3 秒取一张，0–360 秒，共 121 张 800×448 JPEG。
标注目录：`D:\RetailVisionDev\runtime\annotations\2026_10_21_22_12_09_3s`。
其中 `previews/` 是全部中文标注预览，`review/annotation_overview.jpg`
是姿态概览，`labels/` 为 YOLO 标注。未修改原始图片内容。

类别仍为 `0: toy`、`1: white_guitar`，对应“黄色小蜜蜂玩偶”和“白色吉他”。
不标人物、黑色小物体。触角、琴头和可见边界纳入框内，遮挡/截断写入清单。
第 62 张吉他重度遮挡，仅框可见窄边；该图只参与训练。

新数据 121 张、242 个框，合并旧数据后共 323 张、568 个框。
每张来源、媒体时间、原图/标签哈希和划分记录在版本化数据目录
`data/toy_guitar_dataset_v2_20261021/split_manifest.json`。

| 数据 | 训练 | 验证 | 测试 |
| --- | ---: | ---: | ---: |
| 原视频 | 142 | 31 | 29 |
| 新多姿态视频 | 82 | 19 | 20 |
| 合计 | 224 | 50 | 49 |

第一版划分完整保留，避免旧测试图进入训练。新视频姿态变化的第 55、57 张用于
验证，第 52、56、59 张用于测试，其余变化图用于训练；静止段按配置的时间块划分。
划分在训练前固定，测试图不用于训练或选择最佳轮次。
两个录像来自同一摄像头，大量静止姿态跨集合相似；验证报告披露 11,932 对
感知哈希近似的跨集合图片，不属于独立视频、任意位置或目标身份跟踪验收。
此前漏检的实时截图独立保存，不复制进任何训练/验证/测试集合，但其中姿态已由
新视频训练样本覆盖，因此它是故障回归检查，不是全新姿态测试。

## 训练和比较

沿用 YOLO11n、640 输入、GPU 0、batch 16，独立实验名为
`runs/toy_guitar/multipose_v2_20261021`。最多 100 轮，耐心值 25；增加 45 度
随机旋转和 0.15 平移，保留缩放、水平翻转和 mosaic，最后 15 轮关闭 mosaic。
从本地 `data/models/yolo11n.pt` 训练，不下载/替换已有权重。

```powershell
D:/RetailVisionDev/envs/agents-py311/python.exe -B tools/training/train_toy_guitar_v2.py --dry-run
D:/RetailVisionDev/envs/agents-py311/python.exe -B tools/training/train_toy_guitar_v2.py
```

数据目录可在内容校验通过后复用；已有实验目录拒绝覆盖，重新训练需另起实验名。
训练后分别运行第一版与第二版，对同一 49 张测试图按置信度 0.25、类别相同、
IoU ≥ 0.50 逐框匹配，保存 TP/FP/FN，单列新视频及动态测试图。
另对之前漏检的原始实时截图各重复预测两次。中文预测图和完整数值写入实验目录：

- `weights/best.pt`、`weights/last.pt`
- `training_summary.json`、`v1_v2_comparison.json`
- `v1_test_predictions.json`、`v2_test_predictions.json`
- `test_predictions_zh/`、`v1_pinned_replay.jpg`、`v2_pinned_replay.jpg`
- `test_evaluation/`、`results.csv`、`results.png`

该入口只准备数据、训练和离线比较，不切换实时模型、不启动摄像头预览，
也不增加时序跟踪功能。

## 本次结果

75 轮提前停止，最佳权重第 50 轮。此前漏检的原截图在 640/0.25 下恢复检测，
两次均置信度 0.939837、IoU 0.968719；相同回放第一版退出码 2，第二版退出码 0。
新录像留出的 20 张测试图共 40 个目标全部匹配，但其中两张变化图仍有 3 个重复框。

旧录像第 78 张有手持吉他漏标，在评估后人工复核时发现。保留原冻结数据及原始
统计，另存 `heldout_annotation_audit.json` 和 `audited_evaluation_reference.json`。
补充该真实框后，49 张测试图共 88 个目标：第一版 TP/FP/FN = 76/2/12，
第二版 = 87/4/1。第二版对该手持吉他虽有检出，但框偏大未达 IoU 0.50，
剩余三处是重复框。原始 mAP 使用未经纠错的 87 框参考，不应混同于纠错后的统计。

训练后评估曾因列表加载器把图片改名为 `image0.jpg` 中断；修正为逐路径预测并增加
测试后，用 `--evaluate-existing` 接续评估，未重训或改动配置/测试划分。该参数只允许
配置与已完成训练完全一致；9 个聚焦测试与既有 5 个子测试通过。
