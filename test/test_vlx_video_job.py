"""Launcher orchestration and delivery invariants, without model loading."""
import hashlib
import json
import subprocess
import sys
import zipfile

import pytest

from scripts import run_vlx_video_job as job
from test.test_vlx_acceptance_export import make_run


def config():
    return dict(vlx_python=sys.executable, sam_python=sys.executable,
                sam_repository="sam repo", sam_checkpoint="model.pt",
                interaction_config="config.yaml", tracking_chunk_size=150,
                user_missing_grace_seconds=4, ffmpeg_directory="tools")


def completed_fixture(root):
    result = make_run(root, "result")
    (result / "annotated.mp4").write_bytes(b"fixture-media-not-a-real-video")
    job.write(result / "status.json", {"status": "completed"})
    job.write(result / "input.json", {"fixture": True})
    (result / "observations.jsonl").write_text('{}\n', encoding="utf-8")
    frames = result / "frames"
    frames.mkdir()
    (frames / "000000.jpg").write_bytes(b"sample-frame")
    masks = result / "masks_5"
    masks.mkdir()
    job.write(masks / "geometry.json", {"fixture": True})
    duplicate = masks / "chunk_000000"
    duplicate.mkdir()
    (duplicate / "000000.jpg").write_bytes(b"sample-frame")
    raw = root / "raw_vlx_pose"
    raw.mkdir()
    (raw / "observations.jsonl").write_text('{}\n', encoding="utf-8")
    job.write(raw / "manifest.json", {"fixture": True})
    job.write(raw / "status.json", {"status": "completed"})
    job.write(root / "job.json", {"status": "processing_completed"})
    return result


@pytest.mark.parametrize("preset,expected", [("three_toys", "0,1,2"), ("guitar", "4"), ("six_items", "0,1,2,3,4,5")])
def test_presets_preserve_original_class_ids_and_stage_order(tmp_path, preset, expected):
    stages = job.build_stages(config(), tmp_path / '中文 & video.mp4', preset, tmp_path)
    prepare = next(c for n, c in stages if n == "05_准备跟踪")
    assert prepare[prepare.index("--classes") + 1] == expected
    grounding = next(c for n, c in stages if n == "04_VLX识别")
    assert grounding[grounding.index("--video") + 1] == tmp_path / '中文 & video.mp4'
    assert ("07_吉他定位" in dict(stages)) == (preset != "three_toys")
    assert ("06_物品跟踪" in dict(stages)) == (preset != "guitar")
    assert stages[-1][0] == "09_视频完整性"


def test_package_preserves_evidence_facts_and_checksums(tmp_path):
    run = tmp_path / "job"
    run.mkdir()
    result = completed_fixture(run)
    original = (result / "result.json").read_bytes()
    archive, totals = job.package_job(run, tmp_path / "deliveries", "test.zip")
    assert totals["fact_count"] == 3
    assert archive.with_suffix(".zip.sha256").read_text().split()[0] == job.digest(archive)
    with zipfile.ZipFile(archive) as z:
        assert z.read("result.json") == original
        assert "raw_vlx_pose/observations.jsonl" in z.namelist()
        assert "frames/000000.jpg" in z.namelist()
        assert "masks_5/geometry.json" in z.namelist()
        assert not any("chunk_" in n for n in z.namelist())
        facts = json.loads(z.read("interaction_facts.json"))
        assert facts["writes_database"] is False
        assert facts["production_import_ready"] is False
        assert json.loads(z.read("review_status.json"))["accuracy_status"] == "requires_visual_review"
        for line in z.read("FILES_SHA256.txt").decode().splitlines():
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(z.read(name)).hexdigest() == digest


def test_incomplete_run_never_publishes_zip(tmp_path):
    run = tmp_path / "job"
    run.mkdir()
    result = completed_fixture(run)
    job.write(result / "status.json", {"status": "failed"})
    with pytest.raises(ValueError, match="未完成"):
        job.package_job(run, tmp_path / "deliveries", "test.zip")
    assert not (tmp_path / "deliveries").exists()


def test_stage_failure_preserves_job_and_stops_export(tmp_path):
    invoked = []
    def fail(name, command, directory, env):
        invoked.append(name)
        raise RuntimeError("missing runtime")
    with pytest.raises(RuntimeError, match="missing runtime"):
        job.execute(tmp_path / "source.mp4", "guitar", config(), root=tmp_path, runner=fail)
    saved = job.read(next((tmp_path / "runtime").rglob("job.json")))
    assert saved["status"] == "failed"
    assert saved["current_stage"] == "01_环境检查"
    assert len(invoked) == 1
    assert not (tmp_path / "deliveries").exists()


def test_two_runs_do_not_overwrite_and_index_both(tmp_path):
    def simulate(name, command, directory, env):
        if name == "08_计时及视频":
            completed_fixture(directory)
    first = job.execute(tmp_path / "录像.mp4", "six_items", config(), root=tmp_path, runner=simulate)
    before = first.read_bytes()
    second = job.execute(tmp_path / "录像.mp4", "six_items", config(), root=tmp_path, runner=simulate)
    assert first != second and first.read_bytes() == before
    assert first.parent == second.parent == tmp_path / "deliveries/录像"
    index = (tmp_path / "deliveries/README.md").read_text(encoding="utf-8")
    assert first.name in index and second.name in index


def test_cancel_is_recorded_and_never_published(tmp_path):
    def cancel(*args):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        job.execute(tmp_path / "source.mp4", "guitar", config(), root=tmp_path, runner=cancel)
    assert job.read(next((tmp_path / "runtime").rglob("job.json")))["status"] == "cancelled"


def test_console_validates_path_and_menu(tmp_path, monkeypatch):
    video = tmp_path / "中文 空格.mp4"
    video.write_bytes(b"fixture")
    answers = iter(["missing.mp4", f'"{video}"', "0", "9", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert job.select_inputs() == (video.resolve(), "guitar")


def test_real_subprocess_failure_and_paths_with_spaces(tmp_path):
    script = tmp_path / "中文 script.py"
    script.write_text('import sys; print(sys.argv[1]); sys.exit(3)', encoding="utf-8")
    with pytest.raises(RuntimeError, match="退出码 3"):
        job.run_stage("failure", [sys.executable, script, "one & two"], tmp_path, dict(job.os.environ))
    assert "one & two" in (tmp_path / "failure.log").read_text()


def test_lock_rejects_concurrent_launcher_and_releases(tmp_path):
    script = "from pathlib import Path; from scripts.run_vlx_video_job import single_job; import sys\nwith single_job(Path(sys.argv[1])): pass"
    with job.single_job(tmp_path):
        blocked = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)], cwd=job.ROOT, capture_output=True)
        assert blocked.returncode != 0
    resumed = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)], cwd=job.ROOT, capture_output=True)
    assert resumed.returncode == 0, resumed.stderr
