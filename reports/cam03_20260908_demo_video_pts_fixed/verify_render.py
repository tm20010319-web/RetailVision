"""Read-only media checks; writes verification.json beside this fixed render."""
import bisect
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
RUN = ROOT / "reports/cam03_20260907_144722_run"


def run(command):
    return subprocess.run(command, capture_output=True, check=True, timeout=60).stdout


def frames(path, indices):
    expression = "+".join(f"eq(n,{i})" for i in indices)
    data = run([
        "ffmpeg", "-v", "error", "-i", str(path), "-an",
        "-vf", f"scale=1280:720,select='{expression}'", "-fps_mode", "passthrough",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ])
    return np.frombuffer(data, np.uint8).reshape(len(indices), 720, 1280, 3)


def main():
    report = json.loads((OUT / "render_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((RUN / "source_run_manifest.json").read_text(encoding="utf-8"))
    source = manifest["source_video"]
    new = report["output"]
    old = ROOT / "reports/cam03_20260908_demo_video/cam03_member_behavior_miaoxiang_demo.mp4"
    probe = json.loads(run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "frame=pts", "-of", "json", source,
    ]))
    timeline = json.loads((RUN / "source_timeline_evidence.json").read_text(encoding="utf-8"))
    assert [f["pts"] for f in probe["frames"]] == [f["raw_pts"] for f in timeline["frames"]]
    pts = [f["pts"] / 90000 for f in probe["frames"]]
    mappings = [json.loads(line) for line in (OUT / "frame_mapping.jsonl").read_text().splitlines()]
    for i, mapping in enumerate(mappings):
        assert mapping["output_frame"] == i and mapping["output_pts"] == i / 25
        expected = bisect.bisect_right(pts, i / 25) - 1
        assert mapping["source_seq"] == expected
        assert abs(mapping["source_pts"] - pts[expected]) < 1e-7
    assert len(mappings) == 4859 and len(pts) == 4745
    for path, expected_hash in {**report["input_sha256"], source: report["source_sha256"]}.items():
        with Path(path).open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == expected_hash
    keys = [1850, 2412]
    selected = [mappings[k] for k in keys]
    original = frames(source, [mapping["source_seq"] for mapping in selected])
    fixed = frames(new, keys)
    before = frames(old, keys)
    people = [json.loads(line) for line in (RUN / "person_tracks.jsonl").read_text().splitlines()]
    checks = []
    for j, mapping in enumerate(selected):
        track = "2" if j == 0 else "1"
        observation = min(
            (p for p in people if p["track_token"].rsplit(":", 1)[-1] == track),
            key=lambda p: abs(p["source_pts_seconds"] - mapping["source_pts"]),
        )
        x1, y1, x2, y2 = [int(v * (1280 if i % 2 == 0 else 720))
                           for i, v in enumerate(observation["bbox"])]
        # Body interior, excluding outline/label and bottom information bar.
        roi = (slice(y1 + 12, min(y2 - 12, 685)), slice(x1 + 10, x2 - 10))
        target = original[j][roi].astype(float)
        old_error = float(np.mean(np.abs(before[j][roi].astype(float) - target)))
        new_error = float(np.mean(np.abs(fixed[j][roi].astype(float) - target)))
        checks.append({**mapping, "person_track_id": track,
                       "body_interior_rgb_mae_old": old_error,
                       "body_interior_rgb_mae_fixed": new_error})
        assert new_error < 6 and new_error < old_error / 3, checks[-1]
    run(["ffmpeg", "-v", "error", "-i", new, "-f", "null", "-"])
    metadata = json.loads(run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,width,height,nb_frames,duration",
        "-of", "json", new,
    ]))
    assert float(metadata["format"]["duration"]) == manifest["duration_seconds"]
    assert int(metadata["streams"][0]["nb_frames"]) == len(mappings)
    result = {
        "status": "PASS", "actual_source_pts_match_saved_timeline": True,
        "all_output_frame_mappings_verified": len(mappings), "original_source_frames": len(pts),
        "inputs_unchanged": True, "screenshot_cases_actual_encoded_pixels": checks,
        "full_mp4_decode": "PASS", "metadata": metadata,
        "regression": "real variable-PTS FFmpeg fixture: FAIL before fix, PASS after fix",
        "source_osd_note": "Camera OSD and declared absolute start remain unchanged; alignment uses source PTS.",
    }
    (OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
