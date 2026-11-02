# 可选演示

- `greeting/`：宽松的 RTSP Greeting 展示服务（标记为 demo），`python -m demo.greeting`。
- `camera_profile/`：摄像头裁剪与 Ollama 画像，`python -m demo.camera_profile`。
- `product_analytics_agent/`：Python Analytics Harness 手工入口，不是生产 Agent 的答案所有者。
- `legacy_cli.py`：旧历史视频、画像、实时检测演示，`python -m demo.legacy_cli --help`。

自动化测试不得启动真实摄像头、写设备配置或调用 Ollama。
运营台与顾客屏位于根 `frontend/`，与本目录的可选演示分别维护。
