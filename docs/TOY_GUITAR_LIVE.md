# 行为检测-1：黄色小蜜蜂玩偶与白色吉他实时识别

独立预览入口，使用本次训练的YOLO11n `best.pt`。类别0的训练名仍是
`toy`，实时显示明确写为“黄色小蜜蜂玩偶”，语义名为`yellow_bee_toy`；
类别1显示为“白色吉他”。不修改已经完成的训练记录或模型类别编号。

## 已核实的摄像头

- EasyVMS名称：行为检测-1。
- 局域网地址：192.0.2.6。
- 已通过摄像头只读API核对P2P编号，与EasyVMS设备记录一致。
- 实际可用视频：TCP RTSP，端口554，`/live/0/SUB`，800×448，25 FPS。
- 设备返回的RTSP URI中含旧地址192.168.1.11；客户端使用已确认可达的
  192.168.0.40读取相同路径，没有修改设备网络配置。

摄像头凭据来自其现有EasyVMS设备记录：已确认该设备当前使用空密码。
如以后设置密码，通过`TOY_GUITAR_CAMERA_PASSWORD`环境变量提供；
实时配置不保存非空密码。

## 启动与关闭

```powershell
& 'D:\project\RealtimeEntryPipeline-master\scripts\start_toy_guitar_live.ps1'
```

窗口标题为 `Behavior-1 | Yellow Bee & White Guitar | Q to close`。
黄色框对应黄色小蜜蜂玩偶，青色框对应白色吉他。
按Q、Esc或者关闭窗口停止。也可以向runtime目录写入新`stop.request`
文件停止该次运行。

实时参数：`config/toy_guitar_live.yaml`。默认GPU 0、输入640、
confidence=0.25、每秒约10次推理。后台解码只保留最新帧，断流会重新连接；
超过2秒没有新画面时显示等待状态，不继续显示上一次检测为当前结果。

## 文件

- `scripts/run_toy_guitar_live.py`：独立实时入口。
- `scripts/start_toy_guitar_live.ps1`：启动器。
- `test/test_toy_guitar_live.py`：设备身份、类别映射和连接参数测试。
- `D:\RetailVisionDev\runtime\toy-guitar-live-camera1\status.json`：当前运行状态和检测框。
- 同目录`latest.jpg`：每秒更新的识别预览。
- 同目录`camera_identity.json`：已确认的摄像头信息。
- 同目录`live.log`：运行日志。

该入口只读取视频、显示模型结果；没有修改原来的背心配置、SAM3链路、
摄像头参数、业务数据库或正式发布逻辑。

## 首次实机验证

2026-10-21夜视黑白画面：8秒无窗口探测成功处理61帧，约9.1 FPS。
探测结尾识别到黄色小蜜蜂玩偶0.66、白色吉他0.97；这是该时刻
模型置信度，不是准确率。四项针对性测试通过。

训练数据主要是白天的同一位置场景，重新摆放后的识别能力仍需实际检查。
