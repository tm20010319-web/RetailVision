"""
H5AI-W-50PSS-AI 人脸识别能力验证测试
用途：调用设备 API，获取证据证明该设备型号不支持人脸识别功能
"""
import requests
from datetime import datetime

BASE = "http://192.0.2.8"
AUTH = ("admin", "")
HDR = {"Content-Type": "application/xml"}

def test(name, method, path, body=None):
    print(f"\n{'='*70}")
    print(f"测试 {name}")
    print(f"{'='*70}")
    print(f"请求：{method} {BASE}{path}")
    if body:
        print(f"Body：")
        print(body)
    print(f"认证：Basic Auth (admin, 空密码)")
    print(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'─'*70}")

    try:
        r = requests.request(method, f"{BASE}{path}", data=body.encode("utf-8") if body else None, auth=AUTH, headers=HDR, timeout=10)
        print(f"HTTP 状态码：{r.status_code}")
        print(f"响应内容：")
        print(r.text.strip())
        return r.text
    except Exception as e:
        print(f"请求失败：{e}")
        return ""


# ===== 测试 1：查询设备能力声明 =====
test("1. 查询设备能力声明（GET /System/DeviceCap）",
     "GET", "/System/DeviceCap")

# ===== 测试 2：切换 AI 工作模式到 FaceMode =====
test("2. 切换 AI 工作模式到人脸识别（PUT /System/AIWorkMode WorkMode=FaceMode）",
     "PUT", "/System/AIWorkMode",
     '<?xml version="1.0" encoding="UTF-8"?>\n<AIWorkMode>\n    <WorkMode>FaceMode</WorkMode>\n</AIWorkMode>')

# ===== 测试 3：切换 AI 工作模式到 PeopleMode（当前模式，对照测试） =====
test("3. 切换 AI 工作模式到人形检测（PUT /System/AIWorkMode WorkMode=PeopleMode）—— 当前模式对照测试",
     "PUT", "/System/AIWorkMode",
     '<?xml version="1.0" encoding="UTF-8"?>\n<AIWorkMode>\n    <WorkMode>PeopleMode</WorkMode>\n</AIWorkMode>')

# ===== 测试 4：设置设备脸库拥有者 =====
test("4. 设置设备脸库拥有者（PUT /System/FrontDeviceOwnnerInfo）",
     "PUT", "/System/FrontDeviceOwnnerInfo",
     '<?xml version="1.0" encoding="UTF-8"?>\n<FrontDeviceOwnnerInfo>\n    <Ownner>test-owner-1234567890abcdef</Ownner>\n</FrontDeviceOwnnerInfo>')

# ===== 测试 5：创建人脸脸库 =====
test("5. 创建人脸脸库（PUT /FaceGroups/Create）",
     "PUT", "/FaceGroups/Create",
     '<?xml version="1.0" encoding="UTF-8"?>\n<FaceGroup>\n    <GroupName>retail_members</GroupName>\n    <GroupID2>test-group-uuid-001</GroupID2>\n    <Ownner>testsafetycode</Ownner>\n    <SystemTime>2026-07-16T12:00:00</SystemTime>\n</FaceGroup>')

# ===== 测试 6：查询所有人脸脸库 =====
test("6. 查询所有人脸脸库（PUT /FaceGroups/QueryAll）",
     "PUT", "/FaceGroups/QueryAll", "")

print(f"\n{'='*70}")
print("测试完成")
print(f"{'='*70}")
