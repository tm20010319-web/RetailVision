from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_dpi01e2_cam03_offline_member_binding import (
    FrozenBehaviorInputMismatch,
    RecognitionQueryIntegrityError,
    audit_recognition_pages,
    project_identity_facts,
    recording_time_manifest,
    validate_frozen_input,
    validate_recognition_record_scope,
    _formal_camera_configuration,
    RecognitionConfiguration,
    RecognitionRuntime,
    run,
)
from agent.offline.offline_identity import OfflineEventTrackAssociation, OfflineTrackIdentityAggregator
from agent.offline.offline_video_models import OfflineIdentityPassResult


SOURCE = Path(r"D:\GJJ\work\video\2026_09_05_20_33_56.mp4")
REPLAY = Path(r"D:\GJJ\work\dpi01d_cam03_20260905_203356_review\full_replay_rerun2.json")


def test_frozen_input_requires_exactly_twelve_events_and_sha(tmp_path):
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    replay["raw_stage9_events"] = replay["raw_stage9_events"][:-1]
    mutated = tmp_path / "replay.json"
    mutated.write_text(json.dumps(replay), encoding="utf-8")
    with pytest.raises(FrozenBehaviorInputMismatch):
        validate_frozen_input(source_path=SOURCE, replay_path=mutated)


def test_source_sha_mismatch_fails_closed(tmp_path):
    altered_source = tmp_path / "source.mp4"
    altered_source.write_bytes(b"not-the-frozen-source")
    with pytest.raises(FrozenBehaviorInputMismatch, match="source_sha256"):
        validate_frozen_input(source_path=altered_source, replay_path=REPLAY)


def test_time_window_is_computed_from_pts_and_is_half_open():
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    manifest = recording_time_manifest(
        replay,
        "921f6ec3881a8222ee269c767a6a3bab276e95be337b95272a0ad53a9afd2920",
        "a08d40c2203a2148da8c57b1a973aa56120be684932a28d9c311556dfeece10d",
    )
    conversion = manifest["time_conversion"]
    assert conversion["source_pts_seconds"] == 81.241
    assert conversion["utc_start_at"] == "2026-09-04T08:34:52.000"
    assert conversion["utc_end_at"] == "2026-09-04T08:36:13.241"
    start = datetime.fromisoformat(conversion["utc_start_at"])
    end = datetime.fromisoformat(conversion["utc_end_at"])
    validate_recognition_record_scope(
        {"camera_id": "cam03", "occurred_at": start},
        camera_id="cam03", start_at=start, end_at=end,
    )
    with pytest.raises(RecognitionQueryIntegrityError):
        validate_recognition_record_scope(
            {"camera_id": "cam03", "occurred_at": end},
            camera_id="cam03", start_at=start, end_at=end,
        )


def test_camera_scope_and_pagination_fail_closed():
    start = datetime(2026, 9, 4, 8, 34, 52)
    end = start + timedelta(seconds=10)
    record = {"camera_id": "cam02", "occurred_at": start + timedelta(seconds=1)}
    with pytest.raises(RecognitionQueryIntegrityError, match="camera_scope"):
        validate_recognition_record_scope(record, camera_id="cam03", start_at=start, end_at=end)
    page = {"page_num": 1, "records": [{"camera_id": "cam03", "occurred_at": start}], "eof": False, "next_page": 2}
    with pytest.raises(RecognitionQueryIntegrityError, match="duplicate_page"):
        audit_recognition_pages(
            [page, {**page, "page_num": 2, "next_page": None, "eof": True}],
            camera_id="cam03", start_at=start, end_at=end,
        )
    with pytest.raises(RecognitionQueryIntegrityError, match="pagination_loop"):
        audit_recognition_pages(
            [{"page_num": 1, "records": [], "eof": False, "next_page": 1}],
            camera_id="cam03", start_at=start, end_at=end,
        )


def test_person_tracks_are_independent_and_only_confirmed_projects_member():
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    events = replay["raw_stage9_events"]
    start = datetime(2026, 9, 4, 8, 34, 52)
    track_1 = events[-1]["person_track_token"]
    track_2 = events[0]["person_track_token"]
    member, anonymous = project_identity_facts(
        events,
        {
            track_1: {"identity_status": "confirmed", "member_id": "member-1"},
            track_2: {"identity_status": "ambiguous", "member_id": "member-2"},
        },
        start,
    )
    assert member and all(item["member_id"] == "member-1" for item in member)
    assert len(anonymous) == 9
    assert all(item["member_id"] is None for item in anonymous)


class _FakeProvider:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def query(self, start, end):
        self.calls.append((start, end))
        return self.result


class _PassThroughIdentityPass:
    def __init__(self, provider, associations):
        self.provider = provider
        self.associations = associations
        self.calls = []

    def __call__(self, request, start, end):
        self.calls.append((request, start, end))
        result = self.provider.query(start, end)
        return OfflineIdentityPassResult(
            recognition_coverage_status="complete" if result.status == "complete" else "unavailable",
            associations=self.associations,
            failure_reason=result.failure_reason,
            accepted_evidence=result.accepted_evidence,
            record_audits=result.record_audits,
            page_audits=result.page_audits,
        )


def _run_config():
    return RecognitionConfiguration(
        camera_id="cam03", store_id="store-1", device_id="cam03-device",
        base_url_present=True,
        credentials_present={"username": True, "password": True, "unique_code": True},
        capability_profile_available=True, missing=(), available=True,
        raw_path="approved-cam03.json",
    )


def _track_associations(events, status, member_id=None):
    result = {}
    for token in {event["person_track_token"] for event in events}:
        track_id = next(event["person_track_id"] for event in events if event["person_track_token"] == token)
        association = OfflineTrackIdentityAggregator().aggregate(
            analysis_run_id=events[0]["analysis_run_id"],
            track_token=token,
            track_started_at=datetime(2026, 9, 4, 8, 34, 52),
            track_ended_at=datetime(2026, 9, 4, 8, 36, 13),
            associations=(OfflineEventTrackAssociation(
                status=status, member_id=member_id, candidate_member_id=member_id,
                evidence_id=f"evidence-{track_id}", method="test", confidence=0.9,
            ),),
        )
        result[token] = association
    return result


def test_run_complete_empty_query_calls_provider_and_keeps_unmatched(tmp_path):
    events = json.loads(REPLAY.read_text(encoding="utf-8"))["raw_stage9_events"]
    from agent.offline.offline_recognition_provider import RecognitionRecordQueryResult

    provider = _FakeProvider(RecognitionRecordQueryResult(
        status="complete", requested_start=datetime(2026, 9, 4, 8, 34, 52),
        requested_end=datetime(2026, 9, 4, 8, 36, 13, 241000), accepted_evidence=(),
        record_audits=(), pages_fetched=1, raw_record_count=0, partial_record_count=0,
        coverage_complete=True, failure_reason=None,
    ))
    identity_pass = _PassThroughIdentityPass(provider, _track_associations(events, "unmatched"))
    gate = run(
        tmp_path / "empty",
        runtime=RecognitionRuntime(provider=provider, track_supplier=lambda _: (), identity_pass=identity_pass),
        config=_run_config(),
    )
    assert provider.calls
    assert identity_pass.calls
    assert gate["status"] == "BLOCKED"
    assert "DPI_01E_RECOGNITION_QUERY_PASS" in gate["status_codes"]
    assert gate["confirmed_member_event_count"] == 0
    assert gate["anonymous_event_count"] == 12


def test_run_default_identity_pass_uses_guarded_existing_chain(tmp_path):
    events = json.loads(REPLAY.read_text(encoding="utf-8"))["raw_stage9_events"]
    from agent.identity.camera_track_identity import TrackSnapshot
    from agent.offline.offline_recognition_provider import RecognitionRecordQueryResult

    query = RecognitionRecordQueryResult(
        status="complete", requested_start=datetime(2026, 9, 4, 8, 34, 52),
        requested_end=datetime(2026, 9, 4, 8, 36, 13, 241000), accepted_evidence=(),
        record_audits=(), pages_fetched=1, raw_record_count=0, partial_record_count=0,
        coverage_complete=True, failure_reason=None,
    )
    provider = _FakeProvider(query)
    tracks = tuple(
        TrackSnapshot(
            store_id="store-1", camera_id="cam03", runtime_id=events[0]["analysis_run_id"],
            track_id=track_id, track_token=f"store-1:cam03:{events[0]['analysis_run_id']}:{track_id}",
            observed_at=datetime(2026, 9, 4, 8, 34, 56), bbox=(0.1, 0.1, 0.4, 0.9),
            confidence=0.9, role="customer", stable_frames=5,
            is_frame_clipped=False, frame_size=(1280, 720),
        )
        for track_id in (1, 2)
    )
    gate = run(
        tmp_path / "guarded",
        runtime=RecognitionRuntime(provider=provider, track_supplier=lambda _: tracks),
        config=_run_config(),
    )
    assert provider.calls
    assert gate["confirmed_member_event_count"] == 0
    assert gate["anonymous_event_count"] == 12


def test_run_incomplete_query_discards_partial_identity_evidence(tmp_path):
    events = json.loads(REPLAY.read_text(encoding="utf-8"))["raw_stage9_events"]
    from agent.offline.offline_recognition_provider import RecognitionRecordQueryResult

    provider = _FakeProvider(RecognitionRecordQueryResult(
        status="unavailable", requested_start=datetime(2026, 9, 4, 8, 34, 52),
        requested_end=datetime(2026, 9, 4, 8, 36, 13, 241000), accepted_evidence=(),
        record_audits=(), pages_fetched=1, raw_record_count=1, partial_record_count=1,
        coverage_complete=False, failure_reason="pagination_failed",
    ))
    identity_pass = _PassThroughIdentityPass(
        provider, _track_associations(events, "confirmed", "must-not-publish")
    )
    gate = run(
        tmp_path / "partial",
        runtime=RecognitionRuntime(provider=provider, track_supplier=lambda _: (), identity_pass=identity_pass),
        config=_run_config(),
    )
    assert gate["confirmed_member_event_count"] == 0
    assert gate["anonymous_event_count"] == 12
    assert "DPI_01E_RECOGNITION_QUERY_PASS" not in gate["status_codes"]


def test_run_confirmed_and_ambiguous_projection_uses_track_status(tmp_path):
    events = json.loads(REPLAY.read_text(encoding="utf-8"))["raw_stage9_events"]
    from agent.offline.offline_recognition_provider import RecognitionRecordQueryResult

    query = RecognitionRecordQueryResult(
        status="complete", requested_start=datetime(2026, 9, 4, 8, 34, 52),
        requested_end=datetime(2026, 9, 4, 8, 36, 13, 241000), accepted_evidence=(),
        record_audits=(), pages_fetched=1, raw_record_count=0, partial_record_count=0,
        coverage_complete=True, failure_reason=None,
    )
    provider = _FakeProvider(query)
    by_token = {}
    for token, status, member in [
        (events[0]["person_track_token"], "confirmed", "member-electric"),
        (events[-1]["person_track_token"], "ambiguous", None),
    ]:
        by_token.update(_track_associations([event for event in events if event["person_track_token"] == token], status, member))
    identity_pass = _PassThroughIdentityPass(provider, by_token)
    gate = run(
        tmp_path / "mixed",
        runtime=RecognitionRuntime(provider=provider, track_supplier=lambda _: (), identity_pass=identity_pass),
        config=_run_config(),
    )
    assert gate["confirmed_member_event_count"] == 9
    assert gate["anonymous_event_count"] == 3
    assert "DPI_01E_IDENTITY_AMBIGUOUS" in gate["status_codes"]
    member_facts = (tmp_path / "mixed" / "member_bound_behavior_facts.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line)["member_id"] == "member-electric" for line in member_facts)


def test_run_configuration_missing_does_not_call_provider(tmp_path, monkeypatch):
    provider = _FakeProvider(None)
    monkeypatch.delenv("BEHAVIOR_CONFIG_PATH", raising=False)
    gate = run(tmp_path / "missing", runtime=None, config=None)
    assert gate["status"] == "BLOCKED"
    assert gate["confirmed_member_event_count"] == 0
    assert "CAM03_RECOGNITION_CONFIGURATION_UNAVAILABLE" in gate["status_codes"]
    assert provider.calls == []


def test_formal_config_accepts_unauthenticated_durable_push_with_empty_password(
    tmp_path, monkeypatch
):
    fixture = Path(__file__).resolve().parents[1] / "configs/cam02/behavior_cameras.cam02.stage6a.json"
    config = json.loads(fixture.read_text(encoding="utf-8"))
    camera = config["cameras"][0]
    camera["camera_id"] = "cam03"
    camera["recognition"] = {
        "device_id": "cam03",
        "base_url": "http://192.0.2.4",
        "auth_mode": "none",
        "evidence_mode": "durable_push_inbox",
        "capability_profile_id": "p6s_cam03_offline_identity",
        "capability_profile_path_env": "RETAILVISION_CAPABILITY_PROFILE_PATH",
    }
    path = tmp_path / "cam03.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RETAILVISION_CAPABILITY_PROFILE_PATH", str(profile))
    monkeypatch.setenv("CAMERA_FACE_USERNAME", "")
    monkeypatch.setenv("CAMERA_FACE_PASSWORD", "")
    monkeypatch.delenv("CAMERA_FACE_UNIQUE_CODE", raising=False)
    resolved = _formal_camera_configuration(config_path=path)
    assert resolved.available
    assert resolved.auth_mode == "none"
    assert resolved.evidence_mode == "durable_push_inbox"
