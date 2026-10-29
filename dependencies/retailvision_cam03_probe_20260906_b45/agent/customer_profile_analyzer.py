DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:3b"
DEFAULT_OUTPUT_DIR = "../outputs/customer_profile"

CUSTOMER_PROFILE_PROMPT = """
你是门店视觉导购助手。请根据图片中顾客的可见穿搭、动作和场景，生成顾客画像。

要求：
1. 只输出 JSON，不要输出 Markdown 或解释文字。
2. 不要推断年龄、性别、民族、收入、健康等敏感信息。
3. 如果图片信息不足，请写“无法判断”，不要编造。

JSON 字段：
{
  "穿搭风格": "一句话描述顾客穿搭风格和视觉依据",
  "商品兴趣": ["最多5个可能感兴趣的商品品类"],
  "行为状态": "浏览/停留/靠近货架/排队/离店/无法判断",
  "购买意图分": 0,
  "导购建议": ["最多5条具体导购建议"],
  "风险标记": []
}

购买意图分请输出 0 到 100 的整数。顾客有停留、查看、拿取商品等动作时分数更高；只有静态穿搭照时给中性分。
""".strip()
import argparse
import base64
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import requests
from ultralytics import YOLO


DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:3b"
DEFAULT_OUTPUT_DIR = "../outputs/customer_profile"

CUSTOMER_PROFILE_PROMPT = """
你是门店视觉导购助手。请根据图片中顾客的可见穿搭、动作和场景，生成顾客画像。

要求：
1. 只输出 JSON，不要输出 Markdown 或解释文字。
2. 不要推断年龄、性别、民族、收入、健康等敏感信息。
3. 如果图片信息不足，请写“无法判断”，不要编造。

JSON 字段：
{
  "穿搭风格": "一句话描述顾客穿搭风格和视觉依据",
  "商品兴趣": ["最多5个可能感兴趣的商品品类"],
  "行为状态": "浏览/停留/靠近货架/排队/离店/无法判断",
  "购买意图分": 0,
  "导购建议": ["最多5条具体导购建议"],
  "风险标记": []
}

购买意图分请输出 0 到 100 的整数。顾客有停留、查看、拿取商品等动作时分数更高；只有静态穿搭照时给中性分。
""".strip()


@dataclass
class Keyframe:
    source_type: str
    image_path: str
    frame_index: Optional[int]
    timestamp_seconds: Optional[float]
    confidence: float
    bbox: list[int]


def parse_video_source(value: str) -> Any:
    return int(value) if value.isdigit() else value


def clamp_bbox(
    bbox: list[int],
    width: int,
    height: int,
    padding_ratio: float = 0.08,
) -> list[int]:
    x1, y1, x2, y2 = bbox
    pad_x = int((x2 - x1) * padding_ratio)
    pad_y = int((y2 - y1) * padding_ratio)
    return [
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(width, x2 + pad_x),
        min(height, y2 + pad_y),
    ]


def detect_best_person(
    model: YOLO,
    image: Any,
    conf_threshold: float,
    device: str,
) -> Optional[dict[str, Any]]:
    results = model(image, conf=conf_threshold, device=device, verbose=False)
    if not results or results[0].boxes is None:
        return None

    best = None
    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        if cls_id != 0:
            continue
        confidence = float(box.conf[0])
        bbox = [int(v) for v in box.xyxy[0].tolist()]
        if best is None or confidence > best["confidence"]:
            best = {"confidence": confidence, "bbox": bbox}
    return best


def save_customer_crop(image: Any, bbox: list[int], output_path: Path) -> str:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = clamp_bbox(bbox, width, height)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        crop = image
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), crop)
    return str(output_path)

    return str(output_path)


def build_keyframe_from_image(
    image_path: Path,
    model: YOLO,
    output_dir: Path,
    conf_threshold: float,
    device: str,
) -> Keyframe:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")

    person = detect_best_person(model, image, conf_threshold, device)
    if person:
        crop_path = output_dir / f"{image_path.stem}_customer.jpg"
        saved_path = save_customer_crop(image, person["bbox"], crop_path)
        return Keyframe(
            "image",
            saved_path,
            None,
            None,
            person["confidence"],
            person["bbox"],
        )

    fallback_path = output_dir / f"{image_path.stem}_original.jpg"
    output_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(fallback_path), image)
    return Keyframe("image", str(fallback_path), None, None, 0.0, [])


def open_video_capture(video_source: Any) -> cv2.VideoCapture:
    if isinstance(video_source, int):
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
            cap = cv2.VideoCapture(video_source, backend)
            if cap.isOpened():
                return cap
            cap.release()
    return cv2.VideoCapture(video_source)


def extract_video_keyframes(
    video_source: Any,
    model: YOLO,
    output_dir: Path,
    conf_threshold: float,
    device: str,
    sample_interval: int,
    max_keyframes: int,
    max_frames: Optional[int],
    min_gap_frames: int,
) -> list[Keyframe]:
    cap = open_video_capture(video_source)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video source: {video_source}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    candidates: list[dict[str, Any]] = []
    frame_index = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_index += 1

            if max_frames and frame_index > max_frames:
                break
            if frame_index % sample_interval != 0:
                continue

            person = detect_best_person(model, frame, conf_threshold, device)
            if not person:
                continue

            candidates.append(
                {
                    "frame": frame.copy(),
                    "frame_index": frame_index,
                    "timestamp_seconds": frame_index / fps if fps else None,
                    "confidence": person["confidence"],
                    "bbox": person["bbox"],
                }
            )
    finally:
        cap.release()

    selected: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: item["confidence"],
        reverse=True,
    ):
        is_far_enough = all(
            abs(candidate["frame_index"] - item["frame_index"]) >= min_gap_frames
            for item in selected
        )
        if is_far_enough:
            selected.append(candidate)
        if len(selected) >= max_keyframes:
            break

    keyframes: list[Keyframe] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(
        sorted(selected, key=lambda item: item["frame_index"]),
        start=1,
    ):
        image_path = output_dir / f"keyframe_{index:02d}_frame_{item['frame_index']}.jpg"
        saved_path = save_customer_crop(item["frame"], item["bbox"], image_path)
        keyframes.append(
            Keyframe(
                source_type="video",
                image_path=saved_path,
                frame_index=item["frame_index"],
                timestamp_seconds=item["timestamp_seconds"],
                confidence=item["confidence"],
                bbox=item["bbox"],
            )
        )

    return keyframes


def encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as file:
        return base64.b64encode(file.read()).decode("utf-8")


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"Ollama response is not JSON: {text}")
    return json.loads(match.group(0))


def analyze_with_ollama(
    keyframes: list[Keyframe],
    ollama_url: str,
    ollama_model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not keyframes:
        raise ValueError("No keyframes or customer images were extracted.")

    payload = {
        "model": ollama_model,
        "prompt": CUSTOMER_PROFILE_PROMPT,
        "images": [encode_image_base64(item.image_path) for item in keyframes],
        "stream": False,
        "format": "json",
    }

    response = requests.post(ollama_url, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    data = response.json()
    content = data.get("response", "")
    profile = extract_json_object(content)

    return {
        "profile": profile,
        "ollama_model": ollama_model,
        "keyframes": [asdict(item) for item in keyframes],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YOLO keyframe extraction + Ollama customer profile analysis."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default="0",
        help="Image path, video path, or camera id. Default: 0.",
    )
    parser.add_argument("--yolo-model", default="yolov8n.pt", help="YOLO model path.")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--conf-threshold", type=float, default=0.5)
    parser.add_argument("--sample-interval", type=int, default=5)
    parser.add_argument("--max-keyframes", type=int, default=2)
    parser.add_argument("--max-frames", type=int, default=300)
    parser.add_argument("--min-gap-frames", type=int, default=30)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--extract-only",
        "--skip-ollama",
        dest="extract_only",
        action="store_true",
        help="Only extract keyframes, do not call Ollama.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    model = YOLO(args.yolo_model)
    source = Path(args.source)

    if source.exists() and source.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        keyframes = [
            build_keyframe_from_image(
                source,
                model,
                output_dir,
                args.conf_threshold,
                args.device,
            )
        ]
    else:
        keyframes = extract_video_keyframes(
            parse_video_source(args.source),
            model,
            output_dir,
            args.conf_threshold,
            args.device,
            args.sample_interval,
            args.max_keyframes,
            args.max_frames,
            args.min_gap_frames,
        )

    if args.extract_only:
        result = {"keyframes": [asdict(item) for item in keyframes]}
    else:
        result = analyze_with_ollama(
            keyframes,
            args.ollama_url,
            args.ollama_model,
            args.timeout_seconds,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
