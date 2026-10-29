"""Acceptance exchange invariants; never opens or creates a database."""
import json

import pytest

from scripts.export_vlx_acceptance import export_runs, normalize_run


def make_run(tmp_path, name="run", source_hash="a" * 64, completed=True):
    directory = tmp_path / name
    directory.mkdir()
    session = {
        "session_id": "bee_plush:1", "object_key": "bee_plush", "user_id": 2,
        "user_label": "private-name-must-not-export", "member_id": "private-member",
        "pickup_seconds": 1.2, "pickup_confirmed_seconds": 1.6,
        "putdown_seconds": 4.2 if completed else None,
        "putdown_confirmed_seconds": 5.0 if completed else None,
        "holding_seconds": 3.0 if completed else None,
        "last_observed_seconds": 5.0, "status": "completed" if completed else "incomplete",
        "end_reason": "observed_release" if completed else "end_of_video",
    }
    events = [{"event_id": "bee_plush:1:PICK_UP", "event_type": "PICK_UP",
               "session_id": "bee_plush:1", "object_key": "bee_plush", "user_id": 2,
               "timestamp_seconds": 1.2, "confirmed_at_seconds": 1.6}]
    if completed:
        events.append({**events[0], "event_id": "bee_plush:1:HOLDING", "event_type": "HOLDING",
                       "confirmed_at_seconds": 5.0, "ended_at_seconds": 4.2, "duration_seconds": 3.0})
        events.append({**events[0], "event_id": "bee_plush:1:PUT_DOWN", "event_type": "PUT_DOWN",
                       "timestamp_seconds": 4.2, "confirmed_at_seconds": 5.0})
    total = {"pickup_count": 1, "putdown_count": int(completed), "completed_count": int(completed),
             "incomplete_count": int(not completed), "total_holding_seconds": 3.0 if completed else 0,
             "average_holding_seconds": 3.0 if completed else None,
             "max_holding_seconds": 3.0 if completed else None}
    report = {"schema_version": "vlx_interactions_v1", "interaction_policy": "test_policy",
              "time_basis": "source_video_pts_seconds", "source_sha256": source_hash,
              "duration_seconds": 10.0, "sessions": [session], "events": events, "total": total,
              "by_object": [{**total, "object_key": "bee_plush"}],
              "by_user": [{**total, "user_id": 2}],
              "by_user_object": [{**total, "object_key": "bee_plush", "user_id": 2}],
              "users": [{"user_id": 2, "member_id": "private-member", "face_embedding": [1, 2]}],
              "api_key": "private-test-secret"}
    manifest = {"source_video": "D:/private/upload.mp4", "source_sha256": source_hash,
                "writes_database": False, "evidence_provenance": {
                    "recorded_run": "D:/private/recorded", "mode": "recorded_vlx_pose_fresh_sam31_refinement",
                    "fresh_vlx_inference": False, "fresh_sam31_tracking": True,
                    "api_key": "private-test-secret", "groups": []}}
    save(directory / "result.json", report)
    save(directory / "manifest.json", manifest)
    (directory / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return directory


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_exchange_has_three_facts_and_only_interval_contributes_duration(tmp_path):
    source = make_run(tmp_path)
    original = (source / "result.json").read_bytes()
    output = tmp_path / "export"
    data = export_runs([source], output)
    assert data["totals"]["fact_count"] == 3
    assert data["totals"]["completed_holding_seconds"] == 3.0
    assert [f["holding_duration_seconds"] for f in data["facts"]] == [None, 3.0, None]
    assert all(f["started_at"] is None and f["ended_at"] is None for f in data["facts"])
    assert data["production_import_ready"] is False
    assert (source / "result.json").read_bytes() == original
    all_text = "\n".join(p.read_text(encoding="utf-8") for p in output.rglob("*.json"))
    assert "private-" not in all_text and "D:/private" not in all_text
    assert "face_embedding" not in all_text and "member_id" not in all_text
    assert data["sessions"][0]["user_label"] == "用户2"
    native = json.loads(next(output.glob("*/result.json")).read_text(encoding="utf-8"))
    assert native["events"][1]["duration_seconds"] == 3.0


def test_same_local_ids_in_two_recordings_never_merge(tmp_path):
    a = make_run(tmp_path, "a", "a" * 64)
    b = make_run(tmp_path, "b", "b" * 64)
    data = export_runs([a, b], tmp_path / "export")
    assert len({s["interaction_session_id"] for s in data["sessions"]}) == 2
    assert len({s["anonymous_user_key"] for s in data["sessions"]}) == 2
    assert len({f["fact_id"] for f in data["facts"]}) == 6
    again = export_runs([a, b], tmp_path / "export_again")
    assert data["facts"] == again["facts"]


@pytest.mark.parametrize("key,label", [
    ("home_monitor", "家庭监控"), ("ai_eye_massager", "AI眼睛按摩仪"),
])
def test_fresh_home_item_export_has_real_label_without_invented_reuse(tmp_path, key, label):
    source = make_run(tmp_path)
    report = json.loads((source / "result.json").read_text(encoding="utf-8").replace("bee_plush", key))
    save(source / "result.json", report)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["evidence_provenance"] = {"mode": "fresh_inference"}
    save(source / "manifest.json", manifest)
    (source / "events.jsonl").write_text("\n".join(json.dumps(e) for e in report["events"]), encoding="utf-8")
    original = (source / "result.json").read_bytes()
    normalized = normalize_run(source)
    assert normalized["native_result"]["by_object"][0]["object_name"] == label
    assert normalized["metadata"]["evidence_provenance"]["mode"] == "fresh_inference"
    assert "recorded_run_name" not in normalized["metadata"]["evidence_provenance"]
    assert len(normalized["facts"]) == 3
    assert (source / "result.json").read_bytes() == original


def test_fresh_zero_detection_run_exports_empty_facts_without_fabricating_events(tmp_path):
    source = make_run(tmp_path)
    report = json.loads((source / "result.json").read_text(encoding="utf-8"))
    for key in ("sessions", "events", "by_object", "by_user", "by_user_object", "users"):
        report[key] = []
    report["total"] = {k: None if k in ("average_holding_seconds", "max_holding_seconds") else 0
                       for k in report["total"]}
    save(source / "result.json", report)
    (source / "events.jsonl").write_text("", encoding="utf-8")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["evidence_provenance"] = {"mode": "fresh_inference"}
    save(source / "manifest.json", manifest)
    output = tmp_path / "export"
    data = export_runs([source], output)
    assert data["facts"] == [] and data["sessions"] == []
    assert data["totals"]["fact_count"] == 0
    assert (output / "interaction_facts.jsonl").read_text(encoding="utf-8") == ""


def test_reused_evidence_still_requires_its_source_reference(tmp_path):
    source = make_run(tmp_path)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    del manifest["evidence_provenance"]["recorded_run"]
    save(source / "manifest.json", manifest)
    with pytest.raises(ValueError, match="recorded_run"):
        normalize_run(source)


def test_eof_session_never_becomes_completed_holding_or_putdown(tmp_path):
    run = normalize_run(make_run(tmp_path, completed=False))
    assert [f["event_type"] for f in run["facts"]] == ["merchandise_pick_up"]
    assert run["sessions"][0]["holding_seconds"] is None


def test_occlusion_estimate_survives_export_without_nested_private_data(tmp_path):
    source = make_run(tmp_path)
    report = json.loads((source/'result.json').read_text(encoding='utf-8'))
    report['sessions'][0].update(duration_includes_occlusion=True,occlusion_intervals=[{
        'last_seen_seconds':2.0,'reacquired_seconds':3.0,'member_name':'private-secret'}])
    save(source/'result.json',report)
    run = normalize_run(source)
    assert run['sessions'][0]['duration_includes_occlusion'] is True
    assert run['facts'][1]['duration_includes_occlusion'] is True
    assert run['sessions'][0]['occlusion_intervals']==[{'last_seen_seconds':2.0,'reacquired_seconds':3.0}]
    assert 'private-secret' not in json.dumps(run)


@pytest.mark.parametrize('interval',[(0,3),(3,2),(2,7)])
def test_invalid_occlusion_interval_is_rejected(tmp_path,interval):
    source = make_run(tmp_path)
    report = json.loads((source/'result.json').read_text(encoding='utf-8'))
    report['sessions'][0].update(duration_includes_occlusion=True,occlusion_intervals=[{
        'last_seen_seconds':interval[0],'reacquired_seconds':interval[1]}])
    save(source/'result.json',report)
    with pytest.raises(ValueError,match='occlusion'):
        normalize_run(source)


def test_event_interval_must_match_session_not_just_total_count(tmp_path):
    source = make_run(tmp_path)
    report = json.loads((source / "result.json").read_text(encoding="utf-8"))
    report["events"][1]["duration_seconds"] = 8.0
    save(source / "result.json", report)
    (source / "events.jsonl").write_text("\n".join(json.dumps(e) for e in report["events"]), encoding="utf-8")
    with pytest.raises(ValueError, match="timing"):
        export_runs([source], tmp_path / "bad_interval")


@pytest.mark.parametrize("change", ["duration", "counts", "events", "fingerprint"])
def test_inconsistent_inputs_rejected_before_creating_output(tmp_path, change):
    source = make_run(tmp_path)
    report = json.loads((source / "result.json").read_text(encoding="utf-8"))
    if change == "duration":
        report["sessions"][0]["holding_seconds"] = 99
    elif change == "counts":
        report["by_user_object"][0]["pickup_count"] = 2
    elif change == "events":
        report["events"] = []
    else:
        report["source_sha256"] = "b" * 64
    save(source / "result.json", report)
    output = tmp_path / "export"
    with pytest.raises(ValueError):
        export_runs([source], output)
    assert not output.exists()


def test_neither_existing_output_nor_source_directory_can_be_overwritten(tmp_path):
    source = make_run(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        export_runs([source], output)
    assert marker.read_text() == "keep"
    with pytest.raises(ValueError):
        export_runs([source], source / "nested")
    with pytest.raises(ValueError):
        export_runs([source, source], tmp_path / "duplicates")
    assert not (tmp_path / "duplicates").exists()
