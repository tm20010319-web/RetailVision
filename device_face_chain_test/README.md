# 替换摄像头人脸链路测试

本工具用于验证既有会员 API 的注册和人脸上传链路，以及独立的摄像头 HTTP 推送接收器。会员 API 负责创建会员和同步人脸；接收器只接收 `heartbeat` 与 `FaceReco`，读取已有 SQLite 映射并写出测试结果。旧的轮询 Runtime 不会启动。

## 前置条件

本工作区使用以下 Python：

```powershell
$python = 'C:\Users\Administrator\miniconda3\envs\agents-py311\python.exe'
```

选择一个用于测试的 SQLite 数据库，并始终让两个终端使用同一个 `DATABASE_URL`。

## 终端 1：启动会员 API

```powershell
$python = 'C:\Users\Administrator\miniconda3\envs\agents-py311\python.exe'
$env:DATABASE_URL = 'sqlite:///demo_flow.db'
$env:CAMERA_FACE_DEVICE_ID = 'entrance-1'
$env:CAMERA_FACE_BASE_URL = 'http://camera-lan-ip'
$env:CAMERA_FACE_USERNAME = 'camera-api-user'
$env:CAMERA_FACE_PASSWORD = 'camera-api-password'
$env:CAMERA_FACE_UNIQUE_CODE = 'camera-unique-code'
$env:CAMERA_FACE_GROUP_UUID = 'face-group-uuid'
$env:CAMERA_FACE_GROUP_ID = '0'
& $python run_member_api.py
```

终端 1 和终端 2 必须使用完全相同的 `DATABASE_URL` 与 `CAMERA_FACE_DEVICE_ID`。示例中的设备地址、凭据、唯一编码和脸库字段必须替换为当前设备的真实配置。

## 终端 2：启动事件接收器

```powershell
$python = 'C:\Users\Administrator\miniconda3\envs\agents-py311\python.exe'
$env:DATABASE_URL = 'sqlite:///demo_flow.db'
$env:P6S_DEVICE_SERIAL = 'camera-serial-from-device-page'
$env:CAMERA_FACE_DEVICE_ID = 'entrance-1'
$env:P6S_EVENT_HOST = '0.0.0.0'
$env:P6S_EVENT_PORT = '8081'
$env:P6S_SAVE_CAPTURE = 'true'
& $python -m device_face_chain_test.run_receiver
```

`P6S_DEVICE_SERIAL` 是摄像头页面显示的精确物理序列号。`CAMERA_FACE_DEVICE_ID` 是 SQLite 表 `device_member_mappings` 中保存的逻辑设备 ID。二者通过映射明确关联，不要求相同。

请在 Windows 防火墙中允许入站 TCP `8081`，并确认摄像头与演示电脑位于可互通的局域网。

## 摄像头网页配置

在摄像头网页的 HTTP 事件推送配置中填写：

```text
启用：是
协议：HTTP
服务器地址：演示电脑的局域网 IPv4 地址
URL 路径：/api/v1/event/pull
端口：8081
认证：None
```

保存后点击 `Test`。先确认接收器收到并成功响应 `heartbeat`，再继续人脸识别测试。

## 创建会员并上传人脸

以下命令使用既有 API；将示例字段和图片路径替换为测试数据。不要在本文档或终端历史中保存真实密码、设备凭据、序列号、人脸图片或私有 URL。

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/members -H "Content-Type: application/json" -d '{"member_id":"M001","name":"Test Member"}'
curl.exe -X POST "http://127.0.0.1:8000/api/members/M001/face?declared_sex=female" -F "file=@C:\path\to\test-face.jpg"
curl.exe http://127.0.0.1:8000/api/members/M001/face/status
```

以实际 API 返回字段为准确认映射已生成，且 `sync_status=synced`，再站到摄像头前。未同步的人脸不能作为识别链路通过的依据。

## 结果与文件

接收器会写入 `outputs/device_face_chain_test/latest_result.json`，并按事件写入 JSON；当 `P6S_SAVE_CAPTURE=true` 且设备提供有效 JPEG 时，也会写入同名 JPEG。

`status=passed` 且 `reason=passed` 表示序列号、相似度和本地映射均通过。`waiting/unrecognized_face` 表示摄像头没有识别到人脸；`failed` 表示链路验证失败。相同序列号和事件 ID 的重传会在日志和处理结果中标记为 `ignored/duplicate`，但不会创建或重写事件 JSON 和 `latest_result.json`；检查原始事件 JSON。查看 `reason` 可定位失败原因，事件 JSON 还会记录匹配号、逻辑成员 ID、相似度和抓拍保存状态。

## 排障

| 现象或原因 | 检查项 |
| --- | --- |
| 无 heartbeat | 检查摄像头 HTTP 已启用、LAN IPv4、路径、端口、Windows 防火墙和 `Test`。 |
| 有 heartbeat 但无 FaceReco | 在摄像头中启用人脸识别事件和实时上传，确认人脸库已同步。 |
| `mapping_not_found` | 核对 `CAMERA_FACE_DEVICE_ID`、精确 `P6S_DEVICE_SERIAL`，以及 `device_member_mappings` 中对应 `unique_id` 的 `sync_status=synced`。 |
| `similarity_below_threshold` | 调整光线和拍摄距离，或确认 `P6S_SIMILARITY_THRESHOLD` 的预期值。 |
| `database_unavailable` | 确认两个终端使用可访问的同一 SQLite `DATABASE_URL`，且文件路径和权限正确。 |
| `duplicate` | 摄像头可能重传了相同事件；检查事件 ID，首个事件的 JSON 是有效结果。 |
| `invalid_capture` | 设备抓拍不是有效 `data:image/jpeg;base64,...` 数据；识别元数据仍可用于链路判断。 |

本工具不会持久化进店记录，不运行 YOLO 关联，不调用 Ollama，也不启动推荐功能。
