# 真实摄像头顾客画像 Demo

该目录是独立演示应用，不依赖设备人脸识别 API，也不写入会员数据库。

## 启动

先启动并预热 Ollama：

```powershell
ollama run qwen2.5vl:3b
```

在另一个 PowerShell 中设置真实 RTSP 地址。地址只保存在当前终端环境变量中，不要提交到仓库：

```powershell
$env:DEMO_VIDEO_SOURCE=$env:VIDEO_SOURCE
$env:DEMO_OLLAMA_MODEL="qwen2.5vl:3b"
$env:DEMO_OLLAMA_TIMEOUT_SECONDS="45"
$env:DEMO_CAPTURE_COOLDOWN_SECONDS="3"
& "C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe" -m demo.camera_profile
```

也可以直接传入当前终端中的地址：

```powershell
& "C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe" -m demo.camera_profile --source $env:VIDEO_SOURCE
```

页面地址：`http://127.0.0.1:8010`

## 演示流程

1. 页面显示摄像头和模型状态。
2. 顾客进入真实摄像头画面并形成稳定 Track。
3. 页面在最近访客列表显示真实人物裁剪图和 Mock 基本信息。
4. 单线程任务把该裁剪图发送给 Ollama。
5. 成功时显示“真实 Ollama 画像”；失败或超时时显示“演示兜底结果”。

运行数据位于 `demo/camera_profile/data/`，页面最多保留最近五位顾客。
