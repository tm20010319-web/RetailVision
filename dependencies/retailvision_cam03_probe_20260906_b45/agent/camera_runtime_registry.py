"""Store-scoped registry of Behavior Runtime camera instances."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Callable, Optional


@dataclass(frozen=True)
class CameraRuntimeRegistration:
    store_id: str
    camera_id: str
    runtime_instance_id: str
    activated_at: datetime


class CameraRuntimeRegistry:
    """Small deep module for checking which camera runtimes are usable now."""

    def __init__(self, *, clock: Callable[[], datetime] = datetime.utcnow) -> None:
        self.clock = clock
        self._active: dict[str, CameraRuntimeRegistration] = {}
        self._lock = RLock()

    def activate(self, *, store_id: str, camera_id: str, runtime_instance_id: str) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
            store_id, camera_id, runtime_instance_id
        )):
            raise ValueError("store_id, camera_id and runtime_instance_id are required")
        with self._lock:
            self._active[camera_id] = CameraRuntimeRegistration(
                store_id=store_id,
                camera_id=camera_id,
                runtime_instance_id=runtime_instance_id,
                activated_at=self.clock().replace(tzinfo=None),
            )

    def deactivate(self, camera_id: str, *, runtime_instance_id: Optional[str] = None) -> None:
        with self._lock:
            current = self._active.get(camera_id)
            if current is None:
                return
            if runtime_instance_id is not None and current.runtime_instance_id != runtime_instance_id:
                return
            self._active.pop(camera_id, None)

    def resolve(self, camera_id: str, *, store_id: str, runtime_instance_id: str) -> Optional[CameraRuntimeRegistration]:
        with self._lock:
            current = self._active.get(camera_id)
            if current is None:
                return None
            if current.store_id != store_id or current.runtime_instance_id != runtime_instance_id:
                return None
            return current

    def is_active(self, camera_id: str, *, store_id: str, runtime_instance_id: str) -> bool:
        return self.resolve(
            camera_id,
            store_id=store_id,
            runtime_instance_id=runtime_instance_id,
        ) is not None

    def active_camera_ids(self, *, store_id: Optional[str] = None, runtime_instance_id: Optional[str] = None) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(
                camera_id
                for camera_id, value in self._active.items()
                if (store_id is None or value.store_id == store_id)
                and (runtime_instance_id is None or value.runtime_instance_id == runtime_instance_id)
            ))
