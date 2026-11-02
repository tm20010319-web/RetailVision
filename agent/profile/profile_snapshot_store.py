"""Thread-safe transient storage for stable-track customer crops."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Dict, Optional, Tuple, Union

import cv2
import numpy as np

from agent.identity.device_identity import StableTrackEvent

logger = logging.getLogger(__name__)


class ProfileSnapshotStore:
    """Stores one local customer crop for each stable camera track."""

    def __init__(self, output_dir: Union[str, Path]):
        self.output_dir = Path(output_dir)
        self._lock = RLock()
        self._snapshots: Dict[Tuple[str, int], Path] = {}

    def capture(self, event: StableTrackEvent, frame: np.ndarray) -> Optional[str]:
        """Crop and store the stable track image, returning its path on success."""
        if not isinstance(frame, np.ndarray) or frame.ndim < 2:
            logger.warning("Cannot capture profile snapshot for non-image frame")
            return None
        try:
            if len(event.bbox) != 4:
                logger.warning("Cannot capture profile snapshot with invalid bbox")
                return None
            x1, y1, x2, y2 = (int(value) for value in event.bbox)
        except (TypeError, ValueError):
            logger.warning("Cannot capture profile snapshot with malformed bbox")
            return None

        height, width = frame.shape[:2]
        x1 = max(0, min(width, x1))
        y1 = max(0, min(height, y1))
        x2 = max(0, min(width, x2))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            logger.warning("Cannot capture profile snapshot with empty crop")
            return None

        crop = frame[y1:y2, x1:x2].copy()
        key = (event.camera_id, event.track_id)

        with self._lock:
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                path = self.output_dir / f"{uuid.uuid4().hex}.jpg"
                if not cv2.imwrite(str(path), crop):
                    path.unlink(missing_ok=True)
                    logger.warning("Failed to write profile snapshot: %s", path)
                    return None
            except Exception:
                logger.exception("Failed to capture profile snapshot for track_id=%d", event.track_id)
                return None

            self._snapshots[key] = path
            return str(path)

    def get(self, camera_id: str, track_id: int) -> Optional[str]:
        """Return a stored snapshot path only while its file still exists."""
        key = (camera_id, track_id)
        with self._lock:
            path = self._snapshots.get(key)
            if path is None:
                return None
            if not path.is_file():
                self._snapshots.pop(key, None)
                return None
            return str(path)

    def cleanup_expired(self, max_age_seconds: float) -> None:
        """Delete expired files in the snapshot directory and remove stale keys."""
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")

        oldest_allowed = time.time() - max_age_seconds
        with self._lock:
            for path in self.output_dir.glob("*.jpg"):
                try:
                    if path.stat().st_mtime >= oldest_allowed:
                        continue
                    path.unlink(missing_ok=True)
                except FileNotFoundError:
                    continue
                except OSError:
                    logger.exception("Failed to clean profile snapshot: %s", path)

            for key, path in list(self._snapshots.items()):
                if not path.is_file():
                    self._snapshots.pop(key, None)
