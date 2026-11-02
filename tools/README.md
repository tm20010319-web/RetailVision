# 开发与验证工具

- `replay/`：人物/商品关联、商品跟踪和商品动作的阶段回放。
- `acceptance/`：Stage10 资格评估与展示素材工具。
- `harness/product_analytics/`：Python MCP/Agent Harness，测试和手工演示使用。
- `training/`：员工服数据、训练和评估。
- `manual/`：手工 RTSP/HTTP/设备验证。部分旧脚本在运行时会修改设备，不参与 pytest 收集。
- 根部 dense-product / SAM3 probe：当前初始化、标定和受控真实验证。

从仓库根目录通过 `python -m tools.<package>.<module>` 启动分包工具。`scripts/` 保留
对外使用的现行运行入口。不要从生产 MCP 装配导入本目录。

`manual/device_recognition_smoke.py` 的证据辅助函数仍用于回归测试，保留其实现；
仅事件键函数的导入更新为当前 Recognition Intake。
