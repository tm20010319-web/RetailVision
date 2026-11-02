"""Open camera/video capture without importing the legacy YOLO detector."""
from typing import Any

import cv2


def open_video_capture(video_source: Any) -> cv2.VideoCapture:
    if isinstance(video_source, int):
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
            cap = cv2.VideoCapture(video_source, backend)
            if cap.isOpened():
                return cap
            cap.release()
        return cv2.VideoCapture()
    return cv2.VideoCapture(video_source)
