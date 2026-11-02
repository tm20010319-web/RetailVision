import cv2
import time

# ====================== 请修改以下参数 ======================
# 主码流地址（高清，优先测试子码流）
RTSP_URL = "rtsp://192.0.2.20:554/live/0/MAIN"

# 子码流地址（流畅，连通率更高，建议先测这个）
# RTSP_URL = "rtsp://CAMERA_USER:CAMERA_PASSWORD@192.0.2.20:554/live/0/SUB"

# 连接超时等待时间（秒）
CONNECT_TIMEOUT = 10
# ============================================================

def main():
    print("=" * 60)
    print(f"正在连接RTSP流：\n{RTSP_URL}")
    print("=" * 60)
    print("=" * 60)

    # 创建视频捕获对象，指定FFmpeg后端，兼容性最好
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)

    # 强制使用TCP传输，解决UDP丢包导致的连接失败、花屏、卡顿
    cap.set(cv2.CAP_PROP_RTSP_TRANSPORT, cv2.CAP_RTSP_TRANSPORT_TCP)
    # 设置缓冲区大小，降低延迟和卡顿
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)

    # 检查流是否打开成功
    if not cap.isOpened():
        print("\n❌ RTSP流连接失败！")
        print("\n按优先级排查：")
        print("1. 账号密码错误：确认填的是摄像头网页后台的本地admin密码")
        print("2. RTSP未开启：登录摄像头后台 → RTSP设置，确认功能已启用")
        print("3. 端口错误：默认554，若后台修改过端口请同步替换")
        print("4. 流路径错误：确认后台标注的路径是不是 /live/0/MAIN")
        print("5. 网络不通：确认电脑和摄像头同网段，能ping通")
        return

    print("\n✅ RTSP流连接成功！")

    # 获取并打印流基础信息
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"📊 流参数：分辨率 {width} × {height}，标称帧率 {fps:.1f} FPS")
    print("\n按键盘 q 键可退出播放窗口\n")

    frame_count = 0
    start_time = time.time()

    while True:
        ret, frame = cap.read()

        if not ret:
            print("\n❌ 读取视频帧失败，连接已断开")
            print("可能原因：网络波动、摄像头掉线、码流异常")
            break

        frame_count += 1

        # 每秒计算一次实时帧率并叠加到画面
        elapsed = time.time() - start_time
        if elapsed >= 1:
            real_fps = frame_count / elapsed
            cv2.putText(
                frame, f"Real FPS: {real_fps:.1f}",
                (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2
            )
            frame_count = 0
            start_time = time.time()

        # 显示画面
        cv2.imshow("RTSP Debug Viewer", frame)

        # 按 q 退出
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("\n手动退出")
            break

    # 释放资源
    cap.release()
    cv2.destroyAllWindows()
    print("资源已释放，连接关闭")

if __name__ == "__main__":
    main()