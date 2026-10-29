"""Replay recorded raw VLX/pose evidence on exact original source frames.

Recomputes source-pixel tracking and behavior, never calls the models. This
is a diagnostic replay, not fresh inference or accuracy qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
from agent.vlx_interaction import PersonEvidence
from scripts.run_vlx_interaction_video import load_config, run_analysis
from scripts.vlx_interaction_vision import VideoVision


class RecordedPeople:
    def __init__(self, rows):
        self.rows = {r["timestamp_seconds"]: r for r in rows}
        self.users = self
        self.diagnostics = []

    def infer(self, frame, timestamp):
        row = self.rows[timestamp]
        self.diagnostics = row["diagnostics"]["user_association_diagnostics"]
        return [PersonEvidence(**p) for p in row["people"]]


class RecordedVision(VideoVision):
    def __init__(self, config, vlx, rows, users):
        self.config, self.vlx_config = config, vlx
        self.rows = {r["source_frame"]: r for r in rows}
        self.people = RecordedPeople(rows)
        self.recorded_users = users
        self.initialize_tracking()

    def recognition_due(self, sequence, timestamp):
        row = self.rows[sequence]
        if row["timestamp_seconds"] != timestamp:
            raise ValueError("recorded evidence/source timestamp mismatch")
        return row["diagnostics"]["recognition"] is not None

    def recognize(self, frame, sequence, timestamp):
        recorded = self.rows[sequence]["diagnostics"]["recognition"]
        return recorded["raw_output"], recorded["proposal_count"]

    def users_report(self):
        return self.recorded_users


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recorded-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/vlx_interaction_video.yaml")
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()
    cv2.setNumThreads(2)
    manifest = json.loads((args.recorded_run / "manifest.json").read_text(encoding="utf-8"))
    source = Path(manifest["source_video"])
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest["source_sha256"]:
        raise ValueError("source content differs from recorded inference")
    trace = args.recorded_run / "observations.jsonl"
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    users = json.loads((args.recorded_run / "result.json").read_text(encoding="utf-8"))["users"]
    config, vlx = load_config(args.config)
    if config["sample_fps"] != manifest["config"]["sample_fps"]:
        raise ValueError("replay must preserve recorded sampling cadence")
    provenance = {"mode": "recorded_model_source_pixel_replay", "fresh_model_inference": False,
                  "recorded_run": str(args.recorded_run.resolve()),
                  "recorded_trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                  "recomputed": ["source_pixel_tracking", "interaction_decisions"],
                  "reused": ["raw_vlx_output_at_exact_source_frame", "pose_and_anonymous_users"],
                  "recognition_schedule": "original_recorded_frames_only"}
    report = run_analysis(source, args.output_dir, config, vlx, preview=not args.no_preview,
                          vision_factory=lambda c, v: RecordedVision(c, v, rows, users),
                          evidence_provenance=provenance)
    print(json.dumps(report["total"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
