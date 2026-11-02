"""Migration regressions: preserve database identity and isolated composition."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent import paths


@pytest.fixture
def relocated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    destination = tmp_path / "runtime" / "databases"
    destination.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATABASE_DIRECTORY", destination)
    monkeypatch.chdir(tmp_path)
    (destination / "demo_flow.db").write_bytes(b"existing database")
    return tmp_path, destination


def test_old_relative_database_url_keeps_existing_data(relocated_database):
    _, destination = relocated_database
    assert paths.resolve_database_url("sqlite:///demo_flow.db") == (
        "sqlite:///" + (destination / "demo_flow.db").as_posix()
    )


def test_old_absolute_database_url_is_redirected(relocated_database):
    root, destination = relocated_database
    assert paths.resolve_database_url("sqlite:///" + (root / "demo_flow.db").as_posix()) == (
        "sqlite:///" + (destination / "demo_flow.db").as_posix()
    )


def test_existing_old_database_is_never_silently_replaced(relocated_database):
    root, _ = relocated_database
    (root / "demo_flow.db").write_bytes(b"independent database")
    assert paths.resolve_database_url("sqlite:///demo_flow.db") == "sqlite:///demo_flow.db"


@pytest.mark.parametrize("url", [
    "sqlite:///:memory:", "sqlite:///another.db", "sqlite:///demo_flow.db?mode=ro",
    "postgresql://user:password@localhost/store",
])
def test_unrelated_or_special_urls_are_unchanged(relocated_database, url):
    assert paths.resolve_database_url(url) == url


def test_another_worktree_is_not_redirected(relocated_database):
    root, _ = relocated_database
    url = "sqlite:///" + (root.parent / "another-worktree" / "demo_flow.db").as_posix()
    assert paths.resolve_database_url(url) == url


def test_missing_destination_does_not_redirect(relocated_database):
    assert paths.resolve_database_url("sqlite:///retailvision.db") == "sqlite:///retailvision.db"


def test_offline_import_does_not_load_legacy_or_profile(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-B", "-c", (
            "import sys; import pipelines.offline_behavior_pipeline; "
            "assert not any(n.startswith(('agent.legacy.', 'agent.profile.', "
            "'pipelines.legacy.')) for n in sys.modules)"
        )],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
