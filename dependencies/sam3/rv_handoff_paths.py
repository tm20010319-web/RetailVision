"""Location settings for this portable development handoff."""
import json
import os
from pathlib import Path

_configuration = Path(__file__).with_name("rv_handoff_paths.json")
_settings = json.loads(_configuration.read_text(encoding="utf-8"))
CODE_ROOT = Path(os.environ.get("RETAILVISION_CODE_ROOT", _settings["code_root"]))
AGENTS_PYTHON = Path(_settings["agents_python"])
RUNTIME_OUTPUT = Path(_settings["runtime_output"])
os.environ.setdefault("HF_HOME", _settings["hf_home"])
os.environ.setdefault("HF_HUB_CACHE", str(Path(_settings["hf_home"]) / "hub"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

def project_config(name):
    current = CODE_ROOT / "configs" / "cam02" / name
    return current if current.is_file() else CODE_ROOT / name
