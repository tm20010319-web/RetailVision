"""Render the cam03 member-behavior demo video (Miaoxiang style).

Reads ONLY the fixed Run's own artifacts (person/product tracks, raw Stage9
events, source timeline evidence) plus the formal identity associations, and
re-renders from the clean source video. No behavior detection is re-run and
nothing is fabricated: boxes are drawn only when a sampled observation exists
within a small matching window of the displayed frame, and all timers/toasts
are driven by original source PTS.

Output is CFR 25 fps on a real-PTS grid: output frame k (t = k/25) shows the
source frame with the greatest PTS <= t, so playback speed and event timing
match the original recording.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RUN_DIR = Path("D:/GJJ/work/code/reports/cam03_20260907_144722_run")
IDENTITY_DIR = Path("D:/GJJ/work/code/reports/cam03_identity_formal_20260907_212202")
OUT_DIR = Path("D:/GJJ/work/code/reports/cam03_20260908_demo_video_pts_fixed")
OUTPUT_NAME = "cam03_member_behavior_miaoxiang_demo_pts_fixed.mp4"
FIXED_RUN = "cam03-a824bd52328e4e5c8354c1fe6708eb32"
FIXED_SHA = "b7133e373c3d072252bbd2c771bcd0b6e4d9a2fdb8d7638c6982148c24500490"

W, H = 1280, 720
FPS = 25
MATCH_WINDOW = 0.35  # seconds; sampled observations are ~0.2 s apart
CYAN = (0, 229, 255)
GREEN = (72, 220, 118)
PANEL_BG = (16, 20, 26, 178)
TOAST_BG = (12, 16, 22, 200)
WHITE = (255, 255, 255, 255)
GREY = (196, 204, 212, 255)

FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"

def rel_seconds(iso: str, start: datetime) -> float:
    return (datetime.fromisoformat(iso) - start).total_seconds()


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def short_name(product_name: str, key: str) -> str:
    if product_name:
        return product_name.split("（")[0]
    return key


def source_decode_command(source, width, height):
    # Indexing raw frames by original PTS requires exactly one output per
    # original decoded frame. FFmpeg's default CFR sync silently inserts frames.
    return [
        "ffmpeg", "-v", "error", "-i", str(source),
        "-map", "0:v:0", "-an", "-vf", f"scale={width}:{height}",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--identity-dir", type=Path, default=IDENTITY_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    run_dir, identity_dir, output_dir = args.run_dir, args.identity_dir, args.output_dir
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output_directory_not_empty: choose a new directory to preserve previous results")
    manifest = json.loads((run_dir / "source_run_manifest.json").read_text(encoding="utf-8"))
    identity = json.loads((identity_dir / "identity_report.json").read_text(encoding="utf-8"))
    start_utc = datetime.fromisoformat(manifest["utc_start"])
    start_local = datetime.fromisoformat(manifest["local_start"])
    timeline = json.loads((run_dir / "source_timeline_evidence.json").read_text(encoding="utf-8"))
    for record in (manifest, identity, timeline):
        if record["analysis_run_id"] != FIXED_RUN or record["source_sha256"] != FIXED_SHA:
            raise ValueError("source_or_run_scope_mismatch")
    if sha256(manifest["source_video"]) != FIXED_SHA:
        raise ValueError("source_video_hash_mismatch")
    base = Fraction(timeline["stream"]["time_base"])
    start_pts = timeline["stream"]["start_pts"]
    frame_pts = [float(base * (f["raw_pts"] - start_pts)) for f in timeline["frames"]]
    if not frame_pts or frame_pts[0] != 0 or any(a >= b for a, b in zip(frame_pts, frame_pts[1:])):
        raise ValueError("invalid_original_pts_timeline")
    duration = manifest["duration_seconds"]

    people = load_jsonl(run_dir / "person_tracks.jsonl")
    products = load_jsonl(run_dir / "product_tracks.jsonl")
    events = load_jsonl(run_dir / "raw_stage9_events.jsonl")
    associations = load_jsonl(identity_dir / "track_identity_associations.jsonl")
    for row in people + products + events + associations:
        if row["analysis_run_id"] != FIXED_RUN:
            raise ValueError("cross_run_artifact")
    input_paths = [run_dir / name for name in (
        "source_run_manifest.json", "source_timeline_evidence.json", "person_tracks.jsonl",
        "product_tracks.jsonl", "raw_stage9_events.jsonl",
    )] + [identity_dir / "track_identity_associations.jsonl", identity_dir / "identity_report.json"]
    input_hashes = {str(path.resolve()): sha256(path) for path in input_paths}

    confirmed = {}
    for a in associations:
        if a.get("status") == "confirmed" and a.get("member_id"):
            confirmed[a["track_token"]] = {
                "member_id": a["member_id"],
                "from": rel_seconds(a["valid_from"], start_utc) if a.get("valid_from") else 0.0,
                "to": rel_seconds(a["valid_to"], start_utc) if a.get("valid_to") else duration,
            }
    member_display = {"member-gaojunjie": "高俊杰"}

    # per-track person samples sorted by time
    person_by_track: dict[str, list] = {}
    for p in people:
        t = p["source_pts_seconds"]
        person_by_track.setdefault(p["track_token"], []).append((t, p["bbox"]))
    for rows in person_by_track.values():
        rows.sort()
    customer_ordinal = {tok: i + 1 for i, tok in enumerate(sorted(
        person_by_track, key=lambda tok: person_by_track[tok][0][0]))}

    product_by_key: dict[str, list] = {}
    product_names: dict[str, str] = {}
    for p in products:
        t = p["source_pts_seconds"]
        product_by_key.setdefault(p["product_key"], []).append((t, p["bbox"]))
        product_names.setdefault(p["product_key"], p.get("product_name") or p["product_key"])
    for rows in product_by_key.values():
        rows.sort()

    # sessions from raw events: product -> (pick_t, put_t, member_token)
    sessions = []
    by_session: dict[str, dict] = {}
    for e in events:
        t = e["source_pts_seconds"]
        # The product observation export has no display-name field; names come
        # from this same Run's behavior facts, never from another video.
        if e.get("product_name"):
            product_names[e["product_key"]] = e["product_name"]
        s = by_session.setdefault(e["interaction_session_id"], {
            "product_key": e["product_key"],
            "product_name": e.get("product_name") or e["product_key"],
            "person_track_token": e["person_track_token"],
        })
        if e["event_type"] == "PICK_UP":
            s["pick"] = t
        elif e["event_type"] == "PUT_DOWN":
            s["put"] = t
    sessions = sorted(by_session.values(), key=lambda s: s.get("pick", 0.0))

    def nearest(rows, t):
        # binary-free linear pointer would be faster; bisect is fine at this size
        import bisect
        idx = bisect.bisect_left([r[0] for r in rows], t)
        best = None
        for j in (idx - 1, idx):
            if 0 <= j < len(rows):
                dt = abs(rows[j][0] - t)
                if best is None or dt < best[0]:
                    best = (dt, rows[j][1])
        if best is not None and best[0] <= MATCH_WINDOW:
            return best[1]
        return None

    font_title = ImageFont.truetype(FONT_BOLD, 24)
    font_sub = ImageFont.truetype(FONT_PATH, 16)
    font_body = ImageFont.truetype(FONT_PATH, 17)
    font_label = ImageFont.truetype(FONT_PATH, 16)
    font_bar = ImageFont.truetype(FONT_PATH, 15)
    font_toast = ImageFont.truetype(FONT_BOLD, 20)

    def label_for(track_token, t):
        info = confirmed.get(track_token)
        if info and info["from"] <= t <= info["to"]:
            return "会员｜" + member_display.get(info["member_id"], info["member_id"]), True
        return f"顾客 {customer_ordinal.get(track_token, '?')}", False

    def product_status(key, t):
        for s in sessions:
            if s["product_key"] != key:
                continue
            pick, put = s.get("pick"), s.get("put")
            if pick is not None and put is not None:
                if pick <= t < put:
                    return "持有中", t - pick
                if t >= put:
                    return "已放回", put - pick
        return "在展示", None

    def current_interaction(t):
        for s in sessions:
            pick, put = s.get("pick"), s.get("put")
            if pick is not None and put is not None and pick <= t < put:
                who, _ = label_for(s["person_track_token"], t)
                return who.replace("｜", " "), short_name(s["product_name"], s["product_key"]), t - pick
        return None

    def toasts_at(t):
        out = []
        for s in sessions:
            who, _ = label_for(s["person_track_token"], t)
            who = who.replace("会员｜", "").replace("｜", " ")
            name = short_name(s["product_name"], s["product_key"])
            pick, put = s.get("pick"), s.get("put")
            if pick is not None and pick <= t <= pick + 2.5:
                out.append(f"{who} 拿起了 {name}")
            if put is not None and put <= t <= put + 3.0:
                held = put - (pick if pick is not None else put)
                out.append(f"{who} 放下了 {name}｜持有{held:.1f}秒")
        return out

    def rounded(draw, xy, radius, fill):
        draw.rounded_rectangle(xy, radius=radius, fill=fill)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / OUTPUT_NAME
    partial_path = output_dir / (out_path.stem + ".partial.mp4")
    decode = encode = None
    frame_mapping = (output_dir / "frame_mapping.jsonl").open("x", encoding="utf-8")
    try:
        decode = subprocess.Popen(source_decode_command(manifest["source_video"], W, H),
                                  stdout=subprocess.PIPE, bufsize=W * H * 3 * 4)
        encode = subprocess.Popen([
            "ffmpeg", "-v", "error", "-n",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-i", manifest["source_video"], "-map", "0:v:0", "-map", "1:a?", "-c:a", "copy",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(partial_path),
        ], stdin=subprocess.PIPE)

        frame_size = W * H * 3
        total_out = round(duration * FPS)
        src_index = -1
        current = None
        previews = {int(s * FPS): name for s, name in [
            (20.0, "preview_1_before"), (34.0, "preview_2_pickup1"), (45.0, "preview_3_holding"),
            (55.5, "preview_4_putdown1"), (140.0, "preview_5_holding2"), (190.0, "preview_6_end"),
            (74.0, "preview_7_drift_case_74s"), (96.48, "preview_8_drift_case_96s"),
            (144.0, "preview_9_putdown2"),
        ]}

        for k in range(total_out):
            t = k / FPS
            while src_index + 1 < len(frame_pts) and frame_pts[src_index + 1] <= t:
                raw = decode.stdout.read(frame_size)
                if len(raw) < frame_size:
                    raise RuntimeError(f"source_decode_ended_early_at_{src_index + 1}")
                current = np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 3)
                src_index += 1
            if current is None:
                raw = decode.stdout.read(frame_size)
                if len(raw) < frame_size:
                    raise RuntimeError("source_decode_empty")
                current = np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 3)
                src_index += 1

            source_t = frame_pts[src_index]
            frame_mapping.write(json.dumps({"output_frame": k, "output_pts": t,
                                           "source_seq": src_index, "source_pts": source_t}) + "\n")
            img = Image.fromarray(current.copy())
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            # ---- product boxes ----
            for key, rows in product_by_key.items():
                bbox = nearest(rows, source_t)
                if bbox is None:
                    continue
                x1, y1, x2, y2 = [int(v * (W if i % 2 == 0 else H)) for i, v in enumerate(bbox)]
                draw.rectangle((x1, y1, x2, y2), outline=GREEN + (255,), width=3)
                name = short_name(product_names[key], key)
                state, dur = product_status(key, t)
                text = name if state == "在展示" else f"{name}｜{state}"
                tw = draw.textlength(text, font=font_label)
                ly = max(0, y1 - 26)
                rounded(draw, (x1, ly, x1 + tw + 14, ly + 24), 5, TOAST_BG)
                draw.text((x1 + 7, ly + 3), text, font=font_label, fill=WHITE)

            # ---- person boxes ----
            for token, rows in person_by_track.items():
                bbox = nearest(rows, source_t)
                if bbox is None:
                    continue
                x1, y1, x2, y2 = [int(v * (W if i % 2 == 0 else H)) for i, v in enumerate(bbox)]
                text, is_member = label_for(token, source_t)
                draw.rectangle((x1, y1, x2, y2), outline=CYAN + (255,), width=3)
                tw = draw.textlength(text, font=font_label)
                ly = max(0, y1 - 26)
                rounded(draw, (x1, ly, x1 + tw + 14, ly + 24), 5, TOAST_BG)
                draw.text((x1 + 7, ly + 3), text, font=font_label,
                          fill=(0, 229, 255, 255) if is_member else WHITE)

            # ---- top-left product status card ----
            lines = []
            for key in sorted(product_by_key):
                name = short_name(product_names[key], key)
                state, dur = product_status(key, t)
                if state == "持有中":
                    lines.append(f"{name}｜持有中｜{dur:.1f}秒")
                elif state == "已放回":
                    lines.append(f"{name}｜已放回｜持有{dur:.1f}秒")
                else:
                    lines.append(f"{name}｜在展示")
            card_w = max(int(draw.textlength(s, font=font_body)) for s in lines) + 26
            card_h = 16 + 26 * len(lines)
            rounded(draw, (14, 55, 14 + card_w, 55 + card_h), 8, PANEL_BG)
            for i, s in enumerate(lines):
                draw.text((27, 65 + 26 * i), s, font=font_body, fill=WHITE)

            # ---- top-right brand + interaction panel ----
            pw = 332
            px = W - pw - 14
            inter = current_interaction(t)
            ph = 132 if inter is None else 150
            rounded(draw, (px, 14, px + pw, 14 + ph), 8, PANEL_BG)
            draw.text((px + 18, 24), "秒象科技", font=font_title, fill=WHITE)
            draw.text((px + 18, 56), "会员商品行为分析", font=font_sub, fill=GREY)
            draw.line((px + 18, 82, px + pw - 18, 82), fill=(255, 255, 255, 64), width=1)
            draw.text((px + 18, 90), "当前交互", font=font_sub, fill=GREY)
            if inter is None:
                draw.text((px + 18, 112), "暂无商品交互", font=font_body, fill=WHITE)
            else:
                who, name, held = inter
                draw.text((px + 18, 110), f"{who} → {name}", font=font_body, fill=WHITE)
                draw.text((px + 18, 132), f"状态：持有中｜本次持有：{held:.1f}秒",
                          font=font_sub, fill=(140, 235, 170, 255))

            # ---- toasts (bottom-center, above status bar) ----
            for i, text in enumerate(toasts_at(t)):
                tw = draw.textlength(text, font=font_toast)
                tx = (W - tw - 36) / 2
                ty = H - 96 - i * 44
                rounded(draw, (tx, ty, tx + tw + 36, ty + 36), 8, TOAST_BG)
                draw.text((tx + 18, ty + 6), text, font=font_toast, fill=(120, 235, 255, 255))

            # ---- bottom status bar ----
            rounded(draw, (0, H - 30, W, H), 0, (10, 13, 18, 165))
            clock_text = (start_local + timedelta(seconds=t)).strftime("%H:%M:%S")
            draw.text((14, H - 26), f"离线分析回放｜cam03｜{clock_text}", font=font_bar, fill=GREY)
            right = f"PTS {t:6.1f}s"
            draw.text((W - draw.textlength(right, font=font_bar) - 14, H - 26), right,
                      font=font_bar, fill=GREY)

            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            if k in previews:
                img.save(output_dir / (previews[k] + ".jpg"), quality=90)
            encode.stdin.write(np.asarray(img).tobytes())
            if k % (FPS * 20) == 0:
                print(f"rendered {t:.1f}/{duration:.2f}s, original frame {src_index + 1}", flush=True)

        encode.stdin.close()
        if src_index + 1 != len(frame_pts) or decode.stdout.read(1):
            raise RuntimeError("decoded_frame_count_does_not_match_original_pts")
        decode.stdout.close()
        if encode.wait() != 0 or decode.wait() != 0:
            raise RuntimeError("ffmpeg_failed")
        partial_path.rename(out_path)
        report = {"status": "rendered", "analysis_run_id": FIXED_RUN,
                  "source_sha256": FIXED_SHA, "input_sha256": input_hashes,
                  "renderer_sha256": sha256(__file__), "output": str(out_path.resolve()),
                  "output_sha256": sha256(out_path), "output_frames": total_out,
                  "source_frames_used": src_index + 1, "duration_seconds": duration,
                  "decode_fps_mode": "passthrough", "output_fps": FPS,
                  "source_pts_mapping": "greatest original PTS <= output PTS",
                  "observation_match_window_seconds": MATCH_WINDOW,
                  "member_interval_padding_seconds": 0,
                  "product_names": product_names, "preview_frames": previews,
                  "audio": "source stream copied if present"}
        (output_dir / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        frame_mapping.close()
        for process in (encode, decode):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
