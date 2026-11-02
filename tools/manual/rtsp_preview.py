import os

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

import cv2

try:
    cv2.setLogLevel(0)
except AttributeError:
    pass

print(cv2.__version__)
print(cv2.VideoCapture)


def open_camera(index: int):
    backends = [
        ("DirectShow", cv2.CAP_DSHOW),
        ("MSMF", cv2.CAP_MSMF),
    ]
    for name, backend in backends:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            ok, frame = cap.read()
            print(f"camera {index} opened with {name}, read={ok}")
            if ok:
                print("frame shape:", frame.shape)
            return cap
        cap.release()
    return None


cap = None
for camera_index in range(5):
    cap = open_camera(camera_index)
    if cap is not None:
        break

if cap is None:
    print("No camera opened. Check camera connection, privacy permissions, or try a video file path.")
else:
    cap.release()
