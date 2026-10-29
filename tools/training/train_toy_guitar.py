"""Independent toy/guitar dataset validation, YOLO training and held-out review.

Paths are resolved against the project root. The original annotation package,
staff-vest scripts/configuration/weights and production runtime are not edited.
This single-video experiment never claims independent-video qualification.
"""
from __future__ import annotations

import argparse
import collections
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import time

import numpy as np
from PIL import Image
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config/toy_guitar_train.yaml"
SPLITS = ("train", "val", "test")
PROTECTED = ("config/staff_vest_train.yaml", "tools/training/train_staff_vest.py",
             "tools/training/validate_staff_vest_dataset.py", "data/models/staff_vest.pt")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not all(isinstance(config.get(k), dict) for k in ("dataset", "training", "evaluation")):
        raise ValueError("Config must contain dataset, training and evaluation mappings")
    names = config["dataset"]["names"]
    if not isinstance(names, dict) or list(names) != list(range(len(names))) or not names:
        raise ValueError("Class names must have consecutive integer IDs starting at zero")
    if any(not isinstance(v, str) or not v.strip() for v in names.values()):
        raise ValueError("Class names must be non-empty strings")
    ratios = config["dataset"]["split_ratios"]
    if set(ratios) != set(SPLITS) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in ratios.values()) or not math.isclose(sum(ratios.values()), 1):
        raise ValueError("Positive train/val/test ratios must sum to one")
    train = config["training"]
    if train.get("task") != "detect":
        raise ValueError("This entry point supports YOLO detection only")
    for key in ("imgsz", "epochs", "patience", "batch"):
        if isinstance(train.get(key), bool) or not isinstance(train.get(key), int) or train[key] < 1:
            raise ValueError(f"training.{key} must be a positive integer")
    if not isinstance(train.get("name"), str) or Path(train["name"]).name != train["name"] or train["name"] in ("", ".", ".."):
        raise ValueError("Experiment name must be one path component")
    for key in ("confidence", "iou"):
        value = config["evaluation"].get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"evaluation.{key} must be between zero and one")
    minimum = config["dataset"].get("min_box_pixels", 8)
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool) or not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("dataset.min_box_pixels must be positive")
    return config


def parse_labels(text: str, size: tuple[int, int], names: dict, imgsz: int, minimum: float) -> list[list[float]]:
    boxes = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"Label line {line_number}: expected five YOLO fields")
        values = [float(value) for value in fields]
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"Label line {line_number}: non-finite coordinate")
        class_id, cx, cy, w, h = values
        if not class_id.is_integer() or int(class_id) not in names:
            raise ValueError(f"Label line {line_number}: unknown class ID {class_id}")
        if not (0 < w <= 1 and 0 < h <= 1 and 0 <= cx <= 1 and 0 <= cy <= 1):
            raise ValueError(f"Label line {line_number}: invalid normalized coordinate")
        if cx-w/2 < -1e-6 or cy-h/2 < -1e-6 or cx+w/2 > 1+1e-6 or cy+h/2 > 1+1e-6:
            raise ValueError(f"Label line {line_number}: box extends outside image")
        scale = min(imgsz/size[0], imgsz/size[1])
        if min(w*size[0]*scale, h*size[1]*scale) < minimum:
            raise ValueError(f"Label line {line_number}: target below {minimum}px at training size")
        boxes.append([int(class_id), cx, cy, w, h])
    return boxes


def source_records(config: dict) -> tuple[dict, list[dict]]:
    source = resolve_path(config["dataset"]["annotations"])
    manifest = json.loads((source / "annotation_manifest.json").read_text(encoding="utf-8"))
    if {int(k): v for k, v in manifest["classes"].items()} != config["dataset"]["names"]:
        raise ValueError("Configured classes disagree with source annotation manifest")
    windows=config["dataset"].get("stratification_windows",[])
    if len({window["name"] for window in windows}) != len(windows):
        raise ValueError("dataset.stratification_windows names must be unique")
    if any(float(window["start_seconds"])>float(window["end_seconds"]) for window in windows):
        raise ValueError("dataset.stratification_windows must have start_seconds <= end_seconds")
    records = []
    seen = set()
    for entry in manifest["frames"]:
        name = entry["image"]
        if Path(name).name != name or name in seen:
            raise ValueError(f"Invalid or duplicate source filename: {name}")
        seen.add(name)
        image = source / "images" / name
        label = source / "labels" / Path(name).with_suffix(".txt")
        image_hash = sha256(image)
        if image_hash != entry["image_sha256"]:
            raise ValueError(f"Source image hash mismatch: {name}")
        with Image.open(image) as decoded:
            size = decoded.size
            decoded.verify()
        boxes = parse_labels(label.read_text(encoding="utf-8"), size, config["dataset"]["names"], config["training"]["imgsz"], config["dataset"].get("min_box_pixels", 8))
        timestamp = float(entry["source_pts_seconds"])
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Source timestamps must be finite and non-negative")
        matching=[window["name"] for window in windows
                  if float(window["start_seconds"])<=timestamp<=float(window["end_seconds"])]
        if len(matching)>1:
            raise ValueError(f"Source timestamp matches multiple stratification windows: {timestamp}")
        records.append({"image": name, "source_pts_seconds": timestamp,
                        "image_sha256": image_hash, "label_sha256": sha256(label),
                        "size": list(size), "boxes": boxes,
                        "class_ids": sorted({int(box[0]) for box in boxes}),
                        "stratum":matching[0] if matching else "default"})
    if {p.name for p in (source / "images").glob("*.jpg")} != seen:
        raise ValueError("Source manifest and image files disagree")
    if {p.stem for p in (source / "labels").glob("*.txt")} != {Path(n).stem for n in seen}:
        raise ValueError("Missing or orphan source label")
    records.sort(key=lambda row: row["source_pts_seconds"])
    if any(a["source_pts_seconds"] >= b["source_pts_seconds"] for a,b in zip(records,records[1:])):
        raise ValueError("Source timestamps must be unique")
    unique=[];by_hash={}
    for record in records:
        previous=by_hash.get(record["image_sha256"])
        if previous is not None:
            if previous["class_ids"] != record["class_ids"]:
                raise ValueError(f"Exact duplicate source image has conflicting visibility states: {previous['image']} and {record['image']}")
            continue
        by_hash[record["image_sha256"]]=record;unique.append(record)
    return manifest, unique


def split_records(records: list[dict], ratios: dict) -> list[dict]:
    """Chronological blocks within each visibility state; retain absence cases."""
    groups = collections.defaultdict(list)
    for record in sorted(records, key=lambda row: row["source_pts_seconds"]):
        groups[(record["stratum"],tuple(record["class_ids"]))].append(record)
    entries = []
    for state,group in groups.items():
        total=len(group)
        if total < len(SPLITS):
            raise ValueError("Each visibility state needs enough images for all three splits")
        raw=[total*ratios[split] for split in SPLITS]
        counts=[math.floor(value) for value in raw]
        for index in sorted(range(len(SPLITS)),key=lambda i:(raw[i]-counts[i],-i),reverse=True)[:total-sum(counts)]:
            counts[index]+=1
        for empty in (i for i,count in enumerate(counts) if count==0):
            donor=max((i for i,count in enumerate(counts) if count>1),key=lambda i:(counts[i]-raw[i],counts[i]))
            counts[donor]-=1;counts[empty]=1
        if sum(counts)!=total or min(counts)<1:
            raise ValueError(f"Cannot split visibility state {state}: total={total}, counts={counts}")
        if state[0] == "default":
            assignments=[split for split,count in zip(SPLITS,counts) for _ in range(count)]
        else:
            # Motion windows are short. Spread every split across their full
            # time range so training and held-out review both see early,
            # middle and late poses instead of reserving all exit-edge frames.
            assigned=[0]*len(SPLITS);assignments=[]
            for position in range(total):
                candidates=[i for i in range(len(SPLITS)) if assigned[i]<counts[i]]
                index=max(candidates,key=lambda i:((position+1)*counts[i]/total-assigned[i],-i))
                assigned[index]+=1;assignments.append(SPLITS[index])
        for row,split in zip(group,assignments):
            entries.append({**row, "split": split, "dataset_image": f"images/{split}/{row['image']}"})
    return sorted(entries, key=lambda row: row["source_pts_seconds"])


def prepare_dataset(config: dict) -> tuple[Path, dict]:
    manifest, records = source_records(config)
    entries = split_records(records, config["dataset"]["split_ratios"])
    root = resolve_path(config["dataset"]["output"])
    source = resolve_path(config["dataset"]["annotations"])
    names = config["dataset"]["names"]
    source_frame_count=len(manifest["frames"]);duplicate_frames_dropped=source_frame_count-len(records)
    split_manifest = {"source_video": manifest["source_video"], "source_video_sha256": manifest["source_sha256"],
        "source_annotations": str(source), "names": names, "ratios": config["dataset"]["split_ratios"],
        "policy": "chronological_blocks_within_class_presence_after_exact_image_deduplication",
        "source_frame_count":source_frame_count,"exact_duplicate_source_frames_dropped":duplicate_frames_dropped,
        "evaluation_scope": "same_video_temporal_holdout", "entries": entries}
    if root.exists():
        existing = json.loads((root / "split_manifest.json").read_text(encoding="utf-8"))
        if existing != json.loads(json.dumps(split_manifest)):
            raise ValueError("Dataset already exists with different sources or parameters; choose a new dataset.output")
    else:
        root.mkdir(parents=True, exist_ok=False)
        for split in SPLITS:
            (root / "images" / split).mkdir(parents=True)
            (root / "labels" / split).mkdir(parents=True)
        for entry in entries:
            shutil.copy2(source / "images" / entry["image"], root / entry["dataset_image"])
            label_name = Path(entry["image"]).with_suffix(".txt")
            shutil.copy2(source / "labels" / label_name, root / "labels" / entry["split"] / label_name)
        write_json(root / "split_manifest.json", split_manifest)
        (root / "data.yaml").write_text(yaml.safe_dump({"path": root.as_posix(), "train": "images/train", "val": "images/val", "test": "images/test", "nc": len(names), "names": names}, sort_keys=False), encoding="utf-8")
    actual_yaml = yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8"))
    expected_yaml = {"path": root.as_posix(), "train": "images/train", "val": "images/val", "test": "images/test", "nc": len(names), "names": names}
    if actual_yaml != expected_yaml:
        raise ValueError("Prepared data.yaml disagrees with the configured dataset")
    seen_hashes = {}
    hashes = []
    report = {"valid": True, "image_count": len(entries), "source_frame_count":source_frame_count,
              "exact_duplicate_source_frames_dropped":duplicate_frames_dropped,
              "splits": {}, "class_counts": {name: 0 for name in names.values()},
              "evaluation_scope": "same_video_temporal_holdout", "independent_video_evaluation": False,
              "warnings": ["All splits share one source recording and mostly fixed object placements; metrics do not qualify relocation performance."]}
    for split in SPLITS:
        selected = [e for e in entries if e["split"] == split]
        if {p.name for p in (root / "images" / split).glob("*.jpg")} != {e["image"] for e in selected}:
            raise ValueError(f"Image membership mismatch in {split}")
        if {p.stem for p in (root / "labels" / split).glob("*.txt")} != {Path(e["image"]).stem for e in selected}:
            raise ValueError(f"Label membership mismatch in {split}")
        counts = collections.Counter()
        absent = 0
        for entry in selected:
            image = root / entry["dataset_image"]
            label = root / "labels" / split / Path(entry["image"]).with_suffix(".txt")
            if sha256(image) != entry["image_sha256"] or sha256(label) != entry["label_sha256"]:
                raise ValueError(f"Dataset copy/hash mismatch: {image.name}")
            if entry["image_sha256"] in seen_hashes:
                raise ValueError(f"Exact duplicate images: {seen_hashes[entry['image_sha256']]} and {image}")
            seen_hashes[entry["image_sha256"]] = str(image)
            with Image.open(image) as decoded:
                pixels = np.asarray(decoded.convert("L").resize((9,8),Image.Resampling.LANCZOS))
                perceptual_hash = int.from_bytes(np.packbits(pixels[:,1:] < pixels[:,:-1]).tobytes(), "big")
                boxes = parse_labels(label.read_text(encoding="utf-8"), decoded.size, names, config["training"]["imgsz"], config["dataset"].get("min_box_pixels", 8))
            hashes.append((split, image.name, perceptual_hash))
            counts.update(names[int(box[0])] for box in boxes)
            absent += int(1 not in {int(box[0]) for box in boxes})
        if any(counts[name] == 0 for name in names.values()):
            raise ValueError(f"A configured class is missing from split {split}")
        report["splits"][split] = {"images": len(selected), "boxes": sum(counts.values()), "class_counts": dict(counts), "guitar_absent_images": absent}
        for name,count in counts.items():
            report["class_counts"][name] += count
    pairs = []
    pair_count = 0
    for index, left in enumerate(hashes):
        for right in hashes[index+1:]:
            distance = (left[2]^right[2]).bit_count()
            if left[0] != right[0] and distance <= 4:
                pair_count += 1
                if len(pairs) < 20:
                    pairs.append({"left":f"{left[0]}/{left[1]}","right":f"{right[0]}/{right[1]}","distance":distance})
    report.update({"near_duplicate_cross_split_pairs": pair_count, "near_duplicate_examples":pairs, "exact_duplicate_images":0})
    if pair_count:
        report["warnings"].append(f"{pair_count} perceptually similar cross-split pairs retained and disclosed for this single-video baseline.")
    write_json(root / "validation_report.json", report)
    return root, report


def metrics_dict(metrics) -> dict:
    return {key:float(value) for key,value in metrics.results_dict.items()}


def run_training(config: dict, dataset: Path, validation: dict, config_path: Path) -> dict:
    train = dict(config["training"])
    model_path = resolve_path(train.pop("model"))
    project = resolve_path(train.pop("project"))
    run_dir = project / train["name"]
    if run_dir.exists():
        raise ValueError(f"Experiment already exists: {run_dir}; choose a new training.name")
    if not model_path.is_file():
        raise ValueError(f"Local pretrained model is missing: {model_path}")
    if str(train["device"]).lower() == "cpu":
        raise ValueError("This configuration requires GPU training")
    settings_dir = project / "_ultralytics_settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(settings_dir)
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ["YOLO_OFFLINE"] = "true"
    import torch
    import ultralytics
    from ultralytics import YOLO, settings
    if not torch.cuda.is_available():
        raise ValueError("CUDA is unavailable; run with the restored agents-py311 environment")
    # Separate settings use bundled local weights, including Ultralytics' AMP check.
    settings.update({"weights_dir":str(model_path.parent), "runs_dir":str(project), "datasets_dir":str(dataset.parent)})
    protected_before = {name:sha256(PROJECT_ROOT/name) for name in PROTECTED}
    model = YOLO(str(model_path), task=train.pop("task"))
    started = time.perf_counter()

    def progress(trainer):
        write_json(Path(trainer.save_dir) / "progress.json", {"status":"training", "epoch":trainer.epoch+1,
            "configured_epochs":trainer.epochs,"elapsed_seconds":round(time.perf_counter()-started,1),
            "metrics":{k:float(v) for k,v in trainer.metrics.items()}, "updated_at_utc":datetime.now(timezone.utc).isoformat()})

    model.add_callback("on_fit_epoch_end", progress)
    model.train(data=str(dataset/"data.yaml"),project=str(project),exist_ok=False,**train)
    run_dir = Path(model.trainer.save_dir)
    if run_dir.resolve() != (project/config["training"]["name"]).resolve():
        raise ValueError(f"Unexpected experiment output path: {run_dir}")
    training_seconds = time.perf_counter()-started
    (run_dir/"training_config.yaml").write_text(config_path.read_text(encoding="utf-8"),encoding="utf-8")
    required = [run_dir/"weights/best.pt",run_dir/"weights/last.pt",run_dir/"results.csv",run_dir/"results.png",run_dir/"confusion_matrix.png"]
    if not all(path.is_file() for path in required):
        raise ValueError("Training completed without all required model/plot artifacts")
    write_json(run_dir/"progress.json", {"status":"evaluating","training_seconds":round(training_seconds,2)})
    best = YOLO(str(run_dir/"weights/best.pt"))
    kwargs = {"data":str(dataset/"data.yaml"),"imgsz":train["imgsz"],"batch":train["batch"],"device":train["device"],"workers":train["workers"],"plots":True,"project":str(run_dir),"exist_ok":False}
    test = best.val(split="test",name="test_evaluation",**kwargs)
    per_class = []
    for metric_index,class_id in enumerate(test.box.ap_class_index):
        per_class.append({"class_id":int(class_id),"class_name":best.names[int(class_id)],"precision":float(test.box.p[metric_index]),"recall":float(test.box.r[metric_index]),"map50":float(test.box.ap50[metric_index]),"map50_95":float(test.box.ap[metric_index])})
    predictions=[]
    for result in best.predict(source=str(dataset/"images/test"),imgsz=train["imgsz"],device=train["device"],conf=config["evaluation"]["confidence"],iou=config["evaluation"]["iou"],save=True,save_txt=True,save_conf=True,project=str(run_dir),name="test_predictions",exist_ok=False,stream=True,verbose=False):
        predictions.append({"image":Path(result.path).name,"detections":[{"class_id":int(box.cls.item()),"confidence":float(box.conf.item()),"bbox_xyxy":box.xyxy[0].tolist()} for box in result.boxes]})
    write_json(run_dir/"test_predictions.json",predictions)
    with (run_dir/"results.csv").open(newline="",encoding="utf-8") as stream:
        epochs=list(csv.DictReader(stream))
    protected_after={name:sha256(PROJECT_ROOT/name) for name in PROTECTED}
    if protected_before != protected_after:
        raise ValueError("An original staff-vest artifact changed during this run")
    summary={"status":"TRAINED_SINGLE_VIDEO_BASELINE","config":str(config_path.resolve()),"dataset":str(dataset),"experiment":str(run_dir),
        "classes":config["dataset"]["names"],"dataset_validation":validation,"training_parameters":config["training"],
        "completed_epochs":len(epochs),"training_seconds":round(training_seconds,2),"environment":{"python":sys.version.split()[0],"torch":torch.__version__,"ultralytics":ultralytics.__version__,"gpu":torch.cuda.get_device_name(0)},
        "test_metrics":metrics_dict(test),"per_class_test_metrics":per_class,"prediction_confidence":config["evaluation"]["confidence"],
        "best_weights":str(run_dir/"weights/best.pt"),"last_weights":str(run_dir/"weights/last.pt"),"original_staff_vest_files_unchanged":True,"protected_sha256":protected_after,
        "evaluation_scope":"same_video_temporal_holdout","independent_video_evaluation":False,"relocation_qualification":"NOT_VERIFIED"}
    write_json(run_dir/"training_summary.json",summary)
    report=["# Toy/guitar YOLO11n baseline", "", "Status: TRAINED_SINGLE_VIDEO_BASELINE", "",f"Completed epochs: {len(epochs)}; training seconds: {training_seconds:.2f}.","", "## Held-out metrics", "", "| Class | Precision | Recall | mAP50 | mAP50-95 |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in per_class:
        report.append(f"| {row['class_name']} | {row['precision']:.4f} | {row['recall']:.4f} | {row['map50']:.4f} | {row['map50_95']:.4f} |")
    report.extend(["", "## Evaluation boundary", "", "Train/val/test are temporal blocks of the SAME recording, within each object-presence state. Static scenes remain highly similar across splits. These metrics do not qualify different placements, angles, cameras or instance identity. Independent-video/relocation evaluation is NOT VERIFIED.","", "Original staff-vest configuration, scripts and weights passed SHA-256 preservation checks.","", "Best weights: `weights/best.pt`; inspect `test_predictions/` for held-out detection images."])
    (run_dir/"training_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    write_json(run_dir/"progress.json",{"status":"complete","completed_epochs":len(epochs),"summary":str(run_dir/"training_summary.json")})
    return summary


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run",action="store_true",help="Prepare/validate data and show parameters without training")
    args=parser.parse_args()
    try:
        config=load_config(args.config)
        dataset,validation=prepare_dataset(config)
        if args.dry_run:
            print(json.dumps({"dry_run":True,"dataset":str(dataset),"validation":validation,"training":config["training"]},indent=2))
        else:
            summary=run_training(config,dataset,validation,args.config)
            print(json.dumps({"status":summary["status"],"best_weights":summary["best_weights"],"test_metrics":summary["test_metrics"]},indent=2))
        return 0
    except (OSError,ValueError,KeyError,yaml.YAMLError) as error:
        print(f"error: {error}",file=sys.stderr)
        return 1


if __name__=="__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
