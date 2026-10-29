"""Test helpers for isolating local logic from optional runtime services."""

from __future__ import annotations

import sys
import types
from pathlib import Path


def ensure_project_root_on_path() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    root_text = str(project_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return project_root


def install_runtime_stubs() -> None:
    """Provide tiny stand-ins for optional packages used at import time."""

    ensure_project_root_on_path()

    if "ultralytics" not in sys.modules:
        ultralytics = types.ModuleType("ultralytics")

        class YOLO:
            def __init__(self, model_path: str):
                self.model_path = model_path

            def __call__(self, *args, **kwargs):
                return []

        ultralytics.YOLO = YOLO
        sys.modules["ultralytics"] = ultralytics
