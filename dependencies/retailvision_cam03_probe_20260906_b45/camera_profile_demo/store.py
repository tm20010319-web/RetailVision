from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from camera_profile_demo.models import VisitorRecord


class VisitorStore:
    def __init__(
        self,
        json_path: str | Path,
        captures_dir: str | Path,
        *,
        limit: int = 5,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.json_path = Path(json_path)
        self.captures_dir = Path(captures_dir)
        self.limit = limit
        self._records: list[VisitorRecord] = []
        self._lock = RLock()

    def load(self) -> None:
        with self._lock:
            if not self.json_path.exists():
                self._records = []
                return
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
            records = [VisitorRecord.model_validate(item) for item in raw]
            records.sort(key=lambda item: item.captured_at, reverse=True)
            self._records = records[: self.limit]

    def add(self, record: VisitorRecord) -> None:
        with self._lock:
            self._records = [
                item for item in self._records if item.visitor_id != record.visitor_id
            ]
            self._records.append(record.model_copy(deep=True))
            self._records.sort(key=lambda item: item.captured_at, reverse=True)
            self._records = self._records[: self.limit]
            self._persist_locked()

    def update(self, visitor_id: str, **changes: Any) -> VisitorRecord:
        with self._lock:
            for index, record in enumerate(self._records):
                if record.visitor_id != visitor_id:
                    continue
                updated = record.model_copy(update=changes, deep=True)
                updated = VisitorRecord.model_validate(updated.model_dump())
                self._records[index] = updated
                self._persist_locked()
                return updated.model_copy(deep=True)
        raise KeyError(visitor_id)

    def get(self, visitor_id: str) -> VisitorRecord | None:
        with self._lock:
            for record in self._records:
                if record.visitor_id == visitor_id:
                    return record.model_copy(deep=True)
        return None

    def list_recent(self) -> list[VisitorRecord]:
        with self._lock:
            return [record.model_copy(deep=True) for record in self._records]

    def cleanup_unreferenced_captures(self) -> None:
        with self._lock:
            referenced = {Path(record.capture_path).resolve() for record in self._records}
            if not self.captures_dir.exists():
                return
            for path in self.captures_dir.glob("*.jpg"):
                if path.resolve() not in referenced:
                    path.unlink(missing_ok=True)

    def _persist_locked(self) -> None:
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.json_path.with_name(f"{self.json_path.name}.tmp")
        payload = [record.model_dump(mode="json") for record in self._records]
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.json_path)
