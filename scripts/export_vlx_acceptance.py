"""Export anonymous, relative-time VLX results for an isolated acceptance import.

Standard library only. No database connection, model inference, or media copying.
Output must be new; source reports and existing databases are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

LABELS = {
    "orange_cartoon": "橙色卡通玩偶", "white_egg": "白色蛋形玩偶",
    "pink_plush": "粉色毛绒玩偶", "yellow_robot_dog": "黄色机器狗",
    "white_guitar": "白色电吉他", "bee_plush": "小蜜蜂玩偶",
    "home_monitor": "家庭监控", "ai_eye_massager": "AI眼睛按摩仪",
}
SESSION_FIELDS = (
    "session_id", "object_key", "user_id", "pickup_seconds", "pickup_confirmed_seconds",
    "putdown_seconds", "putdown_confirmed_seconds", "holding_seconds",
    "last_observed_seconds", "status", "end_reason", "observation_gaps",
    "attribution_uncertain", "source_gap_return_recoveries", "observed_holding_seconds",
    "duration_includes_occlusion", "occlusion_intervals",
)
EVENT_FIELDS = (
    "event_id", "event_type", "session_id", "object_key", "user_id",
    "timestamp_seconds", "confirmed_at_seconds", "ended_at_seconds", "duration_seconds", "reason",
)
SUMMARY_FIELDS = (
    "pickup_count", "putdown_count", "completed_count", "incomplete_count",
    "total_holding_seconds", "average_holding_seconds", "max_holding_seconds",
)
USER_FIELDS = (
    "user_id", "first_seen_seconds", "last_seen_seconds", "observations",
    "appearance_matched_reentries",
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def pick(row, fields):
    return {key: row[key] for key in fields if key in row}


def anonymous_row(row, fields):
    clean = pick(row, fields)
    if "user_id" in clean:
        uid = clean["user_id"]
        if not isinstance(uid, int) or isinstance(uid, bool) or uid < 1:
            raise ValueError("invalid anonymous user ID")
        clean["user_label"] = f"用户{uid}"
    if "object_key" in clean:
        clean["object_name"] = LABELS[clean["object_key"]]
    return clean


def basename(value):
    return str(value).replace("\\", "/").rsplit("/", 1)[-1]


def time_value(value, duration):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timestamp must be a number")
    if not math.isfinite(value) or not 0 <= value <= duration + .001:
        raise ValueError("timestamp outside source recording")
    return value


def normalize_run(directory, camera_name=None):
    manifest = read_json(directory / "manifest.json")
    report = read_json(directory / "result.json")
    if manifest.get("writes_database") is not False:
        raise ValueError("this exporter requires an explicitly file-only run")
    if report.get("schema_version") != "vlx_interactions_v1":
        raise ValueError("unsupported result schema")
    if report.get("time_basis") != "source_video_pts_seconds":
        raise ValueError("unsupported timestamp basis")
    source_hash = report["source_sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", source_hash) or source_hash != manifest["source_sha256"]:
        raise ValueError("source fingerprint mismatch")
    report_hash = digest(directory / "result.json")
    run_id = f"vlx-{source_hash[:16]}-{report_hash[:12]}"
    duration = report["duration_seconds"]
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid source duration")
    provenance = manifest["evidence_provenance"]
    clean_provenance = pick(provenance, (
        "mode", "fresh_vlx_inference", "fresh_sam31_tracking", "fresh_sam31_grounding",
        "recorded_trace_sha256", "recomputed", "time_basis",
    ))
    if provenance.get("recorded_run"):
        clean_provenance["recorded_run_name"] = basename(provenance["recorded_run"])
    elif provenance.get("mode") != "fresh_inference":
        raise ValueError("reused evidence requires a recorded_run reference")
    clean_provenance["groups"] = [
        {**pick(group, ("sha256", "prompt", "mode")),
         "artifact_name": "/".join(str(group["file"]).replace("\\", "/").split("/")[-2:])}
        for group in provenance.get("groups", [])
    ]
    native = {
        **pick(report, ("schema_version", "interaction_policy", "time_basis", "source_sha256",
                        "duration_seconds", "qualification")),
        "total": pick(report["total"], SUMMARY_FIELDS),
        "sessions": [anonymous_row(s, SESSION_FIELDS) for s in report["sessions"]],
        "events": [anonymous_row(e, EVENT_FIELDS) for e in report["events"]],
        "users": [anonymous_row(u, USER_FIELDS) for u in report.get("users", [])],
        "evidence_provenance": clean_provenance,
    }
    for group, fields in (("by_object", ("object_key",)), ("by_user", ("user_id",)),
                          ("by_user_object", ("user_id", "object_key"))):
        native[group] = [anonymous_row(r, fields + SUMMARY_FIELDS) for r in report[group]]
    meta = {
        "analysis_run_id": run_id, "analysis_job_id": f"upload-{source_hash[:24]}",
        "source_run_name": directory.name, "source_video_filename": basename(manifest["source_video"]),
        "source_video_id": f"sha256:{source_hash}", "source_sha256": source_hash,
        "original_result_sha256": report_hash, "duration_seconds": duration,
        "store_id": None, "camera_id": None, "camera_name": camera_name,
        "camera_name_basis": "conversation_mapping" if camera_name else "unknown",
        "recording_started_at": None, "time_basis": "source_video_pts_seconds",
        "writes_database": False, "database_status": "not_written_by_this_run",
        "evidence_provenance": clean_provenance,
    }
    sessions, facts, seen = [], [], set()
    for s in native["sessions"]:
        sid = s["session_id"]
        if not re.fullmatch(r"[a-z_]+:[1-9][0-9]*", sid) or sid in seen:
            raise ValueError("duplicate or invalid source session ID")
        seen.add(sid)
        start = time_value(s["pickup_seconds"], duration)
        confirmed = time_value(s["pickup_confirmed_seconds"], duration)
        if confirmed < start:
            raise ValueError("pickup confirmation precedes onset")
        end, hold = s.get("putdown_seconds"), s.get("holding_seconds")
        completed = s["status"] == "completed"
        if completed:
            time_value(end, duration)
            released = time_value(s["putdown_confirmed_seconds"], duration)
            if end < confirmed or released < end or not isinstance(hold, (int, float)):
                raise ValueError("invalid completed interval")
            if not math.isfinite(hold) or abs(hold - (end - start)) > .00001:
                raise ValueError("holding duration differs from interval")
        elif end is not None or hold is not None:
            raise ValueError("incomplete session must not claim a completed interval")
        occlusions = s.get("occlusion_intervals", [])
        estimated = s.get("duration_includes_occlusion", False)
        if (not isinstance(occlusions,list) or not isinstance(estimated,bool)
                or estimated != bool(occlusions)):
            raise ValueError("invalid occlusion metadata")
        clean_occlusions = []
        previous_end = start
        for interval in occlusions:
            if not isinstance(interval,dict):
                raise ValueError("invalid occlusion interval")
            left = time_value(interval.get("last_seen_seconds"),duration)
            right = time_value(interval.get("reacquired_seconds"),duration)
            limit = end if completed else s['last_observed_seconds']
            if not previous_end <= left < right <= limit:
                raise ValueError("invalid occlusion interval")
            clean_occlusions.append({"last_seen_seconds":left,"reacquired_seconds":right})
            previous_end = right
        if "occlusion_intervals" in s:
            s["occlusion_intervals"] = clean_occlusions
        common = {
            "analysis_job_id": meta["analysis_job_id"], "analysis_run_id": run_id,
            "source_video_id": meta["source_video_id"], "store_id": None, "camera_id": None,
            "interaction_session_id": f"{run_id}:{sid}", "source_session_id": sid,
            "product_key": s["object_key"], "product_name": s["object_name"],
            "anonymous_user_key": f"{run_id}:user:{s['user_id']}",
            "user_id": s["user_id"], "user_label": s["user_label"],
            "time_basis": "source_video_pts_seconds", "started_at": None, "ended_at": None,
            "attribution_uncertain": s.get("attribution_uncertain", False),
            "duration_includes_occlusion": estimated,
            "occlusion_intervals": clean_occlusions,
        }
        sessions.append({**common, **pick(s, SESSION_FIELDS[3:]), "status": s["status"]})
        def fact(kind, t1, t2, confirmation, holding=None):
            fid = hashlib.sha256(f"{run_id}|{sid}|{kind}".encode()).hexdigest()
            return {**common, "fact_id": fid, "event_type": kind,
                    "started_at_seconds": t1, "ended_at_seconds": t2,
                    "confirmed_at_seconds": confirmation, "holding_duration_seconds": holding}
        facts.append(fact("merchandise_pick_up", start, start, confirmed))
        if completed:
            facts.extend([
                fact("merchandise_holding", start, end, s["putdown_confirmed_seconds"], hold),
                fact("merchandise_put_down", end, end, s["putdown_confirmed_seconds"]),
            ])
    total = native["total"]
    completed_count = sum(s["status"] == "completed" for s in sessions)
    if (len(sessions), completed_count, len(sessions)-completed_count) != (
        total["pickup_count"], total["completed_count"], total["incomplete_count"]
    ) or total["putdown_count"] != completed_count:
        raise ValueError("session counts disagree with saved totals")
    if abs(sum(s.get("holding_seconds") or 0 for s in sessions) - total["total_holding_seconds"]) > .00001:
        raise ValueError("session duration sum disagrees with saved total")
    for key in ("by_object", "by_user", "by_user_object"):
        group = native[key]
        for field in ("pickup_count", "putdown_count", "completed_count", "incomplete_count"):
            if sum(r[field] for r in group) != total[field]:
                raise ValueError(f"{key} disagrees with {field}")
    recorded_events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if recorded_events != report["events"]:
        raise ValueError("event log disagrees with result.json")
    observed_events = Counter((e["session_id"], e["event_type"]) for e in native["events"])
    expected_events = Counter((s["session_id"], "PICK_UP") for s in native["sessions"])
    expected_events.update((s["session_id"], "PUT_DOWN") for s in native["sessions"] if s["status"] == "completed")
    expected_events.update((s["session_id"], "HOLDING") for s in native["sessions"] if s["status"] == "completed")
    if observed_events != expected_events:
        raise ValueError("event counts disagree with sessions")
    session_lookup = {s["session_id"]: s for s in native["sessions"]}
    for e in native["events"]:
        s = session_lookup[e["session_id"]]
        if (e["user_id"], e["object_key"]) != (s["user_id"], s["object_key"]):
            raise ValueError("event attribution disagrees with session")
        pairs = [("timestamp_seconds", "putdown_seconds" if e["event_type"] == "PUT_DOWN" else "pickup_seconds"),
                 ("confirmed_at_seconds", "pickup_confirmed_seconds" if e["event_type"] == "PICK_UP" else "putdown_confirmed_seconds")]
        if e["event_type"] == "HOLDING":
            pairs += [("ended_at_seconds", "putdown_seconds"), ("duration_seconds", "holding_seconds")]
        if any(not math.isclose(e[a], s[b], abs_tol=.00001) for a, b in pairs):
            raise ValueError("event timing disagrees with session")
    return {"metadata": meta, "native_result": native, "sessions": sessions, "facts": facts}


def export_runs(directories, output, camera_names=None):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("output already exists; choose a new directory")
    directories = [Path(p).resolve() for p in directories]
    if any(output.is_relative_to(p) for p in directories):
        raise ValueError("output must not modify a source run directory")
    camera_names = camera_names or [None] * len(directories)
    if len(camera_names) != len(directories):
        raise ValueError("one camera name required for each run")
    runs = [normalize_run(p, camera) for p, camera in zip(directories, camera_names)]
    ids = [r["metadata"]["analysis_run_id"] for r in runs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate analysis run")
    facts = [f for r in runs for f in r["facts"]]
    envelope = {
        "schema_version": "vlx_acceptance_relative_facts_v1",
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "writes_database": False, "production_import_ready": False,
        "missing_production_metadata": ["store_id", "camera_id", "verified_recording_started_at"],
        "runs": [r["metadata"] for r in runs],
        "sessions": [s for r in runs for s in r["sessions"]], "facts": facts,
        "totals": {
            "run_count": len(runs), "session_count": sum(len(r["sessions"]) for r in runs),
            "fact_count": len(facts), "facts_by_event_type": dict(Counter(f["event_type"] for f in facts)),
            "completed_holding_seconds": round(sum(f["holding_duration_seconds"] or 0 for f in facts), 6),
        },
    }
    # Serialize before creating output, so malformed/NaN input cannot leave a partial export.
    json.dumps(envelope, allow_nan=False)
    for r in runs:
        json.dumps(r["native_result"], allow_nan=False)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "interaction_facts.json", envelope)
    with (output / "interaction_facts.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for f in facts:
            stream.write(json.dumps(f, ensure_ascii=False, allow_nan=False) + "\n")
    for r in runs:
        destination = output / r["metadata"]["analysis_run_id"]
        destination.mkdir()
        write_json(destination / "result.json", r["native_result"])
        write_json(destination / "metadata.json", r["metadata"])
    return envelope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--camera-name", action="append", help="Display name only; one per --run")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = export_runs(args.run, args.output_dir, args.camera_name)
    print(json.dumps(result["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
