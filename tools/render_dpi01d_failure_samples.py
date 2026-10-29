"""Render small, artifact-backed videos for the two incomplete DPI-01D cycles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

import cv2


WIDTH, HEIGHT = 1280, 720
FPS = 5.0


def _rows_by_seq(payload: Mapping[str, object]) -> dict[int, dict[str, dict[str, object]]]:
    rows: dict[int, dict[str, dict[str, object]]] = {}
    for raw in payload.get("product_tracks", []):
        if not isinstance(raw, Mapping):
            continue
        seq = int(raw["source_seq"])
        key = str(raw["product_key"])
        rows.setdefault(seq, {})[key] = dict(raw)
    return rows


def _event_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    return [dict(item) for item in payload.get("raw_stage9_events", []) if isinstance(item, Mapping)]


def _color_for_status(status: str) -> tuple[int, int, int]:
    return {
        "ACTIVE": (40, 200, 80),
        "MISSING_PENDING": (0, 215, 255),
        "LOST": (40, 40, 235),
    }.get(status, (150, 150, 150))


def _draw_label(image, text: str, origin: tuple[int, int], color=(255, 255, 255), scale=0.48):
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def _draw_product_panel(image, rows: Mapping[str, Mapping[str, object]], y: int) -> None:
    panel_height = 92
    cv2.rectangle(image, (12, y - 24), (605, y + panel_height), (18, 18, 18), -1)
    for index, key in enumerate(("ai_electric_guitar", "ai_guitar")):
        row = rows.get(key, {})
        status = str(row.get("track_status", "UNOBSERVED"))
        display = str(row.get("display_state", "UNOBSERVED"))
        present = bool(row.get("mask_present", False))
        color = _color_for_status(status)
        line_y = y + index * 42
        _draw_label(image, key, (22, line_y), (255, 255, 255), 0.49)
        _draw_label(
            image,
            f"track={status}  display={display}  mask={'YES' if present else 'NO'}",
            (22, line_y + 20),
            color,
            0.47,
        )
        bbox = row.get("bbox")
        if present and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            # bbox values are normalized to the 1280x720 AI frame.
            x1, x2 = int(round(float(bbox[0]) * WIDTH)), int(round(float(bbox[2]) * WIDTH))
            y1, y2 = int(round(float(bbox[1]) * HEIGHT)), int(round(float(bbox[3]) * HEIGHT))
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            _draw_label(image, key, (x1, max(18, y1 - 6)), color, 0.45)


def _annotate_frame(
    frame,
    *,
    seq: int,
    pts: float,
    rows: Mapping[str, Mapping[str, object]],
    events: list[dict[str, object]],
    clip_name: str,
    fixed_replay: bool,
) -> object:
    image = frame.copy()
    cv2.rectangle(image, (0, 0), (WIDTH, 28), (10, 10, 10), -1)
    _draw_label(image, f"DPI-01D {clip_name} | PTS={pts:.2f}s | source_seq={seq}", (12, 20), (255, 255, 255), 0.52)
    _draw_product_panel(image, rows, 58)

    event_text = ""
    for event in events:
        event_pts = float(event.get("source_pts_seconds", 0.0))
        if abs(event_pts - pts) <= 0.35:
            event_text = f"EVENT {event.get('product_key')} {event.get('event_type')}"
            break
    if event_text:
        cv2.rectangle(image, (625, 12), (1268, 48), (30, 30, 145), -1)
        _draw_label(image, event_text, (638, 36), (255, 255, 255), 0.56)

    if clip_name == "second_electric_cycle":
        if fixed_replay and pts >= 96.8:
            note = "修复后：同一 Track 在约97.62s回架并生成PUT_DOWN；后续周期继续按真实视频计数"
            note_color = (40, 200, 80)
        elif pts >= 89.8 and pts < 108.5:
            note = "模型状态: LOST（约90.02s后无Mask）"
            note_color = (40, 40, 235)
        elif pts >= 108.5:
            note = "画面参考: 约110s可见回架；模型仍未恢复，未生成PUT_DOWN"
            note_color = (0, 215, 255)
        else:
            note = "第二次拿起窗口"
            note_color = (255, 255, 255)
    else:
        note = "AI吉他 124.44s PICK_UP；EOF前未观察到归位/PUT_DOWN（保持未闭合）"
        note_color = (0, 215, 255)
    cv2.rectangle(image, (12, 650), (1268, 715), (12, 12, 12), -1)
    _draw_label(image, note, (22, 677), note_color, 0.58)
    _draw_label(image, "这是诊断样例，不是生产结论；状态来自真实回放 JSON。", (22, 702), (180, 180, 180), 0.42)
    return image


def render_clip(
    video: Path,
    payload: Mapping[str, object],
    *,
    start: float,
    end: float,
    output: Path,
    clip_name: str,
    fixed_replay: bool,
) -> None:
    rows_by_seq = _rows_by_seq(payload)
    events = _event_rows(payload)
    selected = {
        seq: rows
        for seq, rows in rows_by_seq.items()
        if start - 0.5 <= float(next(iter(rows.values())).get("source_pts_seconds", 0.0)) <= end + 0.5
    }
    if not selected:
        raise RuntimeError(f"no replay rows in clip {clip_name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open output video: {output}")
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        writer.release()
        raise RuntimeError(f"cannot open source video: {video}")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            seq = int(capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            row_map = rows_by_seq.get(seq)
            if row_map is None:
                continue
            pts = float(next(iter(row_map.values())).get("source_pts_seconds", 0.0))
            if pts < start or pts > end:
                continue
            frame = cv2.resize(frame, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)
            writer.write(_annotate_frame(frame, seq=seq, pts=pts, rows=row_map, events=events, clip_name=clip_name, fixed_replay=fixed_replay))
    finally:
        capture.release()
        writer.release()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.replay.read_text(encoding="utf-8"))
    fixed_replay = any(
        str(item.get("product_key")) == "ai_electric_guitar"
        and str(item.get("event_type")) == "PUT_DOWN"
        and 85.0 <= float(item.get("source_pts_seconds", 0.0)) <= 112.0
        for item in payload.get("raw_stage9_events", [])
        if isinstance(item, Mapping)
    )
    render_clip(
        args.video,
        payload,
        start=85.0,
        end=112.0,
        output=args.output_dir / "dpi01d_second_electric_cycle_diagnostic.mp4",
        clip_name="second_electric_cycle",
        fixed_replay=fixed_replay,
    )
    render_clip(
        args.video,
        payload,
        start=121.0,
        end=139.28,
        output=args.output_dir / "dpi01d_ai_guitar_eof_diagnostic.mp4",
        clip_name="ai_guitar_eof",
        fixed_replay=fixed_replay,
    )
    print(json.dumps({"output_dir": str(args.output_dir.resolve())}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
