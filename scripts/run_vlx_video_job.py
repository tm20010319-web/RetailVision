"""Fixed-preset local video workflow. Reuses existing inference/rules; no DB writes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_vlx_acceptance import export_runs

PRESETS = {
    "three_toys": ("三件玩偶：橙色卡通、白色蛋形、粉色毛绒", (0, 1, 2)),
    "guitar": ("白色电吉他", (4,)),
    "six_items": ("原有六件：三件玩偶、黄色机器狗、白色电吉他、小蜜蜂", (0, 1, 2, 3, 4, 5)),
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def project_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_settings(path):
    import yaml
    settings = read(path)
    chunk = settings.get("tracking_chunk_size", 150)
    if type(chunk) is not int or not 2 <= chunk <= 1000:
        raise ValueError("tracking_chunk_size 必须是 2–1000 的整数；建议保留 150")
    refined = yaml.safe_load(project_path(settings["refinement_config"]).read_text(encoding="utf-8"))
    for key in ("sam_python", "sam_repository", "sam_checkpoint", "interaction_config"):
        refined[key] = str(project_path(refined[key]).resolve())
    refined["vlx_python"] = str(project_path(settings["vlx_python"]).resolve())
    refined["tracking_chunk_size"] = chunk
    refined["ffmpeg_directory"] = str(project_path(settings["ffmpeg_directory"]).resolve())
    for key in ("vlx_python", "sam_python", "sam_checkpoint", "interaction_config"):
        if not Path(refined[key]).is_file():
            raise FileNotFoundError(f"缺少 {key}: {refined[key]}")
    for key in ("sam_repository", "ffmpeg_directory"):
        if not Path(refined[key]).is_dir():
            raise FileNotFoundError(f"缺少 {key}: {refined[key]}")
    for binary in ("ffmpeg.exe", "ffprobe.exe") if os.name == "nt" else ("ffmpeg", "ffprobe"):
        if not (Path(refined["ffmpeg_directory"]) / binary).is_file():
            raise FileNotFoundError(f"缺少 {binary}")
    return refined


def select_inputs(video=None, preset=None):
    while video is None:
        answer = input("录像完整路径（可粘贴带引号的路径，q 退出）：").strip()
        if answer.lower() == "q":
            raise KeyboardInterrupt
        candidate = Path(answer.strip('"'))
        if candidate.is_file():
            video = candidate
        else:
            print("录像文件不存在，请重新输入。")
    video = Path(video).resolve()
    if not video.is_file():
        raise FileNotFoundError(f"录像文件不存在：{video}")
    if preset is None:
        keys = list(PRESETS)
        for i, key in enumerate(keys, 1):
            print(f"{i}. {PRESETS[key][0]}")
        while preset is None:
            answer = input("物品组合编号（q 退出）：").strip()
            if answer.lower() == "q":
                raise KeyboardInterrupt
            if answer.isdigit() and 1 <= int(answer) <= len(keys):
                preset = keys[int(answer)-1]
            else:
                print("请输入列表中的编号。")
    if preset not in PRESETS:
        raise ValueError("未知物品组合")
    return video, preset


@contextmanager
def single_job(root):
    """OS releases lock on crashes too; the persistent file is not a stale lock."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".launcher.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("已有固定入口任务运行中，请等待完成后再运行。") from error
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def run_stage(name, command, job, env):
    print(f"\n正在执行：{name}；日志：{job / (name + '.log')}", flush=True)
    with (job / (name + ".log")).open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(v) for v in command], cwd=ROOT, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            while True:
                try:
                    code = process.wait(timeout=20)
                    break
                except subprocess.TimeoutExpired:
                    progress = ""
                    for folder in ("result", "raw_vlx_pose"):
                        try:
                            status = read(job / folder / "status.json")
                            progress = json.dumps(status, ensure_ascii=False)
                            break
                        except (OSError, ValueError):
                            pass
                    print(f"{name} 仍在运行 {progress}", flush=True)
        except BaseException:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.terminate()
                process.wait()
            raise
    if code:
        raise RuntimeError(f"{name} 失败（退出码 {code}），详见 {name}.log")


def build_stages(config, video, preset, job):
    python, sam = config["vlx_python"], config["sam_python"]
    classes = PRESETS[preset][1]
    csv = ",".join(map(str, classes))
    result, raw = job / "result", job / "raw_vlx_pose"
    script = lambda name: ROOT / "scripts" / name
    refine = lambda stage: [python, "-B", script("vlx_mask_refinement.py"), stage, "--output-dir", result]
    stages = [
        ("01_环境检查", [python, "-B", script("run_vlx_interaction_video.py"), "--check", "--config", config["interaction_config"]]),
        ("02_SAM环境检查", [sam, "-B", "-c", "import sys; sys.path.insert(0,sys.argv[1]); import torch; from sam3.model_builder import build_sam3_predictor; assert torch.cuda.is_available(), 'SAM requires CUDA'", config["sam_repository"]]),
        ("03_录像检查", [str(Path(config["ffmpeg_directory"]) / "ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", video]),
        ("04_VLX识别", [python, "-B", script("run_vlx_interaction_video.py"), "--video", video, "--config", config["interaction_config"], "--output-dir", raw, "--no-preview"]),
        ("05_准备跟踪", refine("prepare") + ["--recorded-run", raw, "--classes", csv]),
    ]
    toys = [i for i in classes if i != 4]
    for label, selected, mode, prompt in (("06_物品跟踪", toys, "track", "toy" if 3 in toys or 5 in toys else "stuffed toy"),
                                          ("07_吉他定位", [4] if 4 in classes else [], "ground", "guitar")):
        if selected:
            command = [sam, "-B", script("vlx_mask_refinement.py"), mode, "--output-dir", result,
                       "--classes", ",".join(map(str, selected)), "--prompt", prompt,
                       "--checkpoint", config["sam_checkpoint"], "--sam-repository", config["sam_repository"]]
            if mode == "track":
                command += ["--chunk-size", config["tracking_chunk_size"]]
            stages.append((label, command))
    stages += [
        ("08_计时及视频", refine("render") + ["--user-missing-grace-seconds", config["user_missing_grace_seconds"]]),
        ("09_视频完整性", [str(Path(config["ffmpeg_directory"]) / "ffmpeg"), "-v", "error", "-xerror", "-i", result / "annotated.mp4", "-f", "null", "-"]),
    ]
    return stages


def package_job(job, destination, archive_name):
    result = job / "result"
    for name in ("annotated.mp4", "result.json", "events.jsonl", "manifest.json", "observations.jsonl", "input.json"):
        if not (result / name).is_file():
            raise ValueError(f"结果缺少 {name}，停止交付")
    if read(result / "status.json").get("status") != "completed":
        raise ValueError("识别未完成，停止交付")
    for name in ("observations.jsonl", "manifest.json", "status.json"):
        if not (job / "raw_vlx_pose" / name).is_file():
            raise ValueError(f"原始识别记录缺少 {name}，停止交付")
    facts = export_runs([result], job / "export")
    review = {"accuracy_status": "requires_visual_review", "writes_database": False,
              "incomplete_sessions": sum(s["status"] != "completed" for s in facts["sessions"]),
              "occlusion_estimated_sessions": sum(s.get("duration_includes_occlusion", False) for s in facts["sessions"]),
              "note": "自动结果需复核；零次不代表没有动作。未观测到放下不编造完成时长。"}
    write(job / "review_status.json", review)
    (job / "README_结果包说明.md").write_text(
        "# VLX 自动结果包\n\n自动处理完成不代表准确性验收通过，需结合录像复核。\n"
        "annotated.mp4 为标注视频；result.json、events.jsonl 为自动统计；interaction_facts.json 为导出事实，未入库。\n"
        "时间为原录像相对秒数，未确认摄像头及实际开始时间。用户编号仅在本录像内有效。\n"
        "遮挡推定保留标记；漏检与未完成会话不能当作已确认完整行为。\n"
        "raw_vlx_pose/ 保留原始识别；frames/ 保留采样帧；masks_*/ 保留跟踪证据；重复的 chunk 帧硬链接不重复打包。\n"
        "job.json 保存本次输入、阶段及配置；日志保留在运行目录，压缩包不包含环境、权重、数据库和原始录像。\n",
        encoding="utf-8")
    files = {}
    for base, prefix in ((result, ""), (job / "raw_vlx_pose", "raw_vlx_pose/"), (job / "export", "export/")):
        for path in base.rglob("*"):
            rel = path.relative_to(base)
            if path.is_file() and path.suffix.lower() in (".json", ".jsonl", ".jpg", ".png", ".mp4", ".md"):
                if any(part.startswith("chunk_") for part in rel.parts[:-1]):
                    continue
                files[prefix + rel.as_posix()] = path
    for name in ("interaction_facts.json", "interaction_facts.jsonl"):
        files[name] = files.pop("export/" + name)
    for name in ("job.json", "review_status.json", "README_结果包说明.md"):
        files[name] = job / name
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / archive_name
    partial = target.with_suffix(".zip.partial")
    if target.exists() or partial.exists() or target.with_suffix(".zip.sha256").exists():
        raise FileExistsError("交付文件已存在，禁止覆盖")
    checks = []
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            checks.append(f"{digest(path)}  {name}")
            archive.write(path, name)
        archive.writestr("FILES_SHA256.txt", "\n".join(checks) + "\n")
    with zipfile.ZipFile(partial) as archive:
        for line in checks:
            expected, name = line.split("  ", 1)
            with archive.open(name) as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError(f"压缩包校验失败：{name}")
    archive_hash = digest(partial)
    partial.rename(target)
    with target.with_suffix(".zip.sha256").open("x", encoding="utf-8") as stream:
        stream.write(f"{archive_hash}  {target.name}\n")
    return target, facts["totals"]


def execute(video, preset, config, *, root=ROOT, runner=run_stage):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", video.stem).strip(" .")
    if not stem or stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        stem = "video_" + hashlib.sha256(str(video).encode()).hexdigest()[:12]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    job = root / "runtime/vlx-video-jobs" / stem / run_id
    job.mkdir(parents=True, exist_ok=False)
    state = {"video": str(video), "preset": preset, "classes": PRESETS[preset][1], "config": config,
             "run_id": run_id, "writes_database": False, "status": "running",
             "current_stage": "准备流程", "stages": []}
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env["PATH"] = config["ffmpeg_directory"] + os.pathsep + env.get("PATH", "")
    write(job / "job.json", state)
    print(f"本次运行目录：{job}", flush=True)
    try:
        for name, command in build_stages(config, video, preset, job):
            state["current_stage"] = name
            write(job / "job.json", state)
            runner(name, command, job, env)
            state["stages"].append({"name": name, "status": "completed"})
        state.update(status="processing_completed", current_stage="10_导出打包")
        write(job / "job.json", state)
        archive, totals = package_job(job, root / "deliveries" / stem, f"RetailVision-{preset}-{run_id}.zip")
        state.update(status="completed", archive=str(archive), totals=totals)
        write(job / "job.json", state)
        index = root / "deliveries/README.md"
        with index.open("a", encoding="utf-8") as stream:
            stream.write(f"\n- VLX `{preset}` / `{run_id}`：[结果包]({archive.relative_to(index.parent).as_posix()})，待人工复核。\n")
        print(f"\n处理和打包完成，结果待复核：{archive}\nSHA256：{archive}.sha256", flush=True)
        return archive
    except BaseException as error:
        state.update(status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
        write(job / "job.json", state)
        print(f"\n处理未完成：{state['current_stage']}。保留记录：{job}", flush=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--preset", choices=PRESETS)
    parser.add_argument("--settings", type=Path, default=ROOT / "config/vlx_video_launcher.json")
    parser.add_argument("--check", action="store_true", help="只检查环境，不加载权重或分析录像")
    args = parser.parse_args(argv)
    try:
        config = load_settings(args.settings)
        if args.check:
            probe = ROOT / "runtime/vlx-video-jobs" / ("check_" + uuid.uuid4().hex[:8])
            probe.mkdir(parents=True)
            env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
            env["PATH"] = config["ffmpeg_directory"] + os.pathsep + env.get("PATH", "")
            for name, command in build_stages(config, Path("unused"), "three_toys", probe)[:2]:
                run_stage(name, command, probe, env)
            print("环境检查通过；没有执行模型推理。")
            return 0
        print("VLX 录像识别：已有物品组合。结果需人工复核；Ctrl+C 取消。")
        video, preset = select_inputs(args.video, args.preset)
        with single_job(ROOT / "runtime/vlx-video-jobs"):
            execute(video, preset, config)
        return 0
    except KeyboardInterrupt:
        print("已取消。")
        return 130
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
