"""Read-only readiness evidence for the frozen September 6 cam03 run."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from agent.models import DeviceRecognitionInbox, DeviceMemberIdentityRevision, PushCoverageObservation
from agent.offline_identity_sql import SqlAlchemyHistoricalIdentityRepository
from agent.offline_recognition_provider import DeviceRecognitionAcceptancePolicy, DurablePushInboxRecognitionProvider
from agent.push_coverage import PushCoverageStore
from scripts.run_dpi01e2_cam03_offline_member_binding import (
    _formal_camera_configuration, load_configured_profiles as _load_configured_profiles,
    resolve_configured_profile, build_recognition_runtime,
)
from scripts.run_cam03_evidence_capture import sha, source_timeline, confirmed_window
from fractions import Fraction

SOURCE = Path('D:/GJJ/work/video/2026_09_06_19_16_00.mp4')
REPLAY = ROOT/'reports/cam03_20260906_behavior_validation/replay/full_replay.json'
SOURCE_SHA = '48c1cdbb5d4e5da2c77add261859f5304b8517a302abf3b23f23422fd976051d'
REPLAY_SHA = 'a77708bd2b67019da5c60a859d0520a69627ab6e673f468a52c27be1afb155d0'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--camera-config', type=Path)
    args = parser.parse_args()
    config = _formal_camera_configuration(config_path=args.camera_config)
    if config.camera_id != 'cam03' or config.device_id != 'cam03' or not config.store_id:
        raise ValueError('CAM03_CONFIGURATION_SCOPE_UNAVAILABLE')
    if args.output.exists():
        raise ValueError('non_overwriting_output_required')
    if sha(SOURCE) != SOURCE_SHA or sha(REPLAY) != REPLAY_SHA:
        raise ValueError('FROZEN_BEHAVIOR_INPUT_MISMATCH')
    replay = json.loads(REPLAY.read_text(encoding='utf-8'))
    events = replay['raw_stage9_events']
    if len(events) != 6 or replay['source_sha256'] != SOURCE_SHA:
        raise ValueError('FROZEN_BEHAVIOR_INPUT_MISMATCH')
    stream, pts, end_pts = source_timeline(SOURCE)
    duration = Fraction(stream['time_base']) * (end_pts-pts[0])
    if abs(float(duration)-113.201) > .000001:
        raise ValueError('source_duration_changed')
    start, end = confirmed_window('2026-09-06T19:11:00+08:00', 'previous_video_overlay_reading_reused_by_operator_request', duration)
    if any(not 0 <= e['source_pts_seconds'] < float(duration) for e in events):
        raise ValueError('event_outside_video_window')
    def connect():
        c = sqlite3.connect('file:D:/GJJ/work/code/demo_flow.db?mode=ro', uri=True)
        c.execute('PRAGMA query_only=ON')
        return c
    engine = create_engine('sqlite://', creator=connect)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as s:
        earliest = s.query(func.min(DeviceMemberIdentityRevision.effective_from)).scalar()
        rows = s.query(DeviceRecognitionInbox).filter(
            DeviceRecognitionInbox.device_id == 'cam03', DeviceRecognitionInbox.source == 'push',
            DeviceRecognitionInbox.occurred_at >= start, DeviceRecognitionInbox.occurred_at < end).all()
        histories = s.query(DeviceMemberIdentityRevision).filter_by(device_id='cam03').all()
        latest = s.query(PushCoverageObservation).filter(
            PushCoverageObservation.device_id == 'cam03', PushCoverageObservation.device_metadata.is_not(None)
        ).order_by(PushCoverageObservation.observed_at.desc()).first()
    history = SqlAlchemyHistoricalIdentityRepository(sessions, history_available_from=earliest).resolver()
    policy = DeviceRecognitionAcceptancePolicy.create(
        policy_id='device-recognition', policy_version='v1', similarity_threshold='0.75',
        accepted_rule_ids=(1,), accepted_pass_codes=(None,0), require_reco_valid=False)
    coverage_store = PushCoverageStore(sessions)
    coverage = coverage_store.coverage(device_id='cam03',start_at=start,end_at=end)
    metadata = (latest.device_metadata or {}) if latest else {}
    def field(key):
        value = metadata.get(key, {})
        return value.get('normalized_value') if isinstance(value, dict) else None
    resolved = resolve_configured_profile(config, metadata)
    profiles, profile_load_reason = _load_configured_profiles(config)
    # The recorded analysis frame is 1280x720, not the source video's 2560x1440.
    initialization = REPLAY.parent.parent/'initialization_committed/initialization_mapping.json'
    analysis_size = tuple(json.loads(initialization.read_text(encoding='utf-8'))['frame_size'])
    face_sizes = {tuple((r.face_spatial_evidence or {}).get('source_frame_size', ())) for r in rows}
    face_size = next(iter(face_sizes)) if len(face_sizes) == 1 else ()
    geometry_reason = (resolved.runtime_geometry_reason(source_frame_size=face_size, target_frame_size=analysis_size)
                       if face_size else 'face_frame_metadata_unavailable')
    provider_reason = resolved.production_eligibility_reason(device_id='cam03')
    formal_provider_eligible = config.available and not provider_reason and not geometry_reason
    if formal_provider_eligible:
        runtime = build_recognition_runtime(
            config, profile_resolution=resolved, historical_identity_resolver=history,
            track_supplier=lambda *_args, **_kwargs: (), session_factory=sessions,
            push_coverage_store=coverage_store,
        )
        provider = runtime.provider
        policy = provider.acceptance_policy
    else:
        # Explicit diagnostic only; a failed profile never becomes qualified.
        provider = DurablePushInboxRecognitionProvider(
            session_factory=sessions, push_coverage_store=coverage_store,
            historical_identity_resolver=history, acceptance_policy=policy,
            device_id='cam03', profile_resolution=None)
    result = provider.query(start, end)
    diagnostics_path = REPLAY.parent/'dpi01d_window_diagnostics.json'
    tokens = set()
    def walk(value):
        if isinstance(value, dict):
            for k,v in value.items():
                if 'token' in k and isinstance(v,str) and v.startswith('store-1:cam03:'):
                    tokens.add(v)
                walk(v)
        elif isinstance(value,list):
            for v in value:
                walk(v)
    walk(json.loads(diagnostics_path.read_text(encoding='utf-8')))
    track_results = [dict(person_track_token=t, status='unavailable', member_id=None,
                         raw_behavior_event_count=sum(e['person_track_token']==t for e in events),
                         reasons=['same_run_person_observations_missing','historical_mapping_unavailable']
                         + ([provider_reason or geometry_reason] if not formal_provider_eligible else [])) for t in sorted(tokens)]
    c = connect()
    registrations = {table:c.execute(f'SELECT count(*) FROM {table} WHERE {column}=?',('cam03',)).fetchone()[0]
                     for table,column in [('device_store_registrations','device_id'),
                                          ('camera_store_registrations','camera_id'),
                                          ('device_camera_registrations','device_id')]}
    formal_count = c.execute('SELECT count(*) FROM behavior_events WHERE camera_id=? AND occurred_at>=? AND occurred_at<?',
                             ('cam03',start.isoformat(sep=' '),end.isoformat(sep=' '))).fetchone()[0]
    c.close()
    report = dict(
        status='OLD_SAMPLE_ANONYMOUS_NEW_SAMPLE_REQUIRED', generated_at=datetime.now(timezone.utc).isoformat(),
        source_sha256=SOURCE_SHA, frozen_replay_sha256=REPLAY_SHA, frozen_event_count=6,
        query_window=dict(start=start.isoformat(),end=end.isoformat(),interval='[start,end)',timezone='Asia/Shanghai',
                          time_source='previous_video_overlay_reading_reused_by_operator_request',
                          prior_evidence=str(ROOT/'reports/cam03_20260906_behavior_validation/validation_summary.json'),
                          prior_evidence_sha256=sha(ROOT/'reports/cam03_20260906_behavior_validation/validation_summary.json'),
                          duration_seconds=float(duration),stream=stream,decoded_frame_count=len(pts)),
        original_events_unmodified=True, prior_experiments_preserved=True,
        old_recording_usable_for_anonymous_behavior=True, old_recording_eligible_for_member_binding=False,
        current_history_count=len(histories), current_history_effective_from=[r.effective_from.isoformat() for r in histories],
        historical_identity_for_recording=[dict(evidence_id=r.canonical_event_id, **asdict(history.resolve_at(
            device_id='cam03',group_id=r.group_id,person_id=r.person_id,unique_id=r.unique_id,occurred_at=r.occurred_at))) for r in rows],
        same_run_person_snapshots_available=bool(replay.get('person_tracks')), new_export_entry='scripts/run_cam03_evidence_capture.py',
        diagnostic_provider_query_executed=not formal_provider_eligible, diagnostic_query_status=result.status,
        coverage=asdict(coverage), recognition_record_count=len(rows),
        acceptance_policy=asdict(policy), accepted_evidence_count=len(result.accepted_evidence),
        record_audits=[asdict(r) for r in result.record_audits],
        formal_provider_executed=formal_provider_eligible, formal_provider_eligible=formal_provider_eligible,
        capability_resolution=dict(status=resolved.status, reason=resolved.reason,
                                   configured_profile_loaded=bool(profiles),load_reason=profile_load_reason,
                                   loaded_profile_ids=[p.profile_id for p in profiles]),
        formal_configuration=dict(camera_id=config.camera_id, store_id=config.store_id,device_id=config.device_id,
                                  config_path=config.raw_path,evidence_mode=config.evidence_mode,profile_id=config.capability_profile_id,
                                  missing=list(config.missing)),
        fingerprint=dict(serial_present=bool(field('device_serial')),model=field('device_model'),
                         firmware=field('firmware_version'),evidence_id=latest.id if latest else None),
        cam03_registration_counts=registrations,
        spatial_geometry=dict(status='verified' if not geometry_reason else 'unavailable',
                              face_frame_size=list(face_size),recording_frame_size=[stream['width'],stream['height']],
                              analysis_frame_size=list(analysis_size), reason=geometry_reason,
                              implementation='existing_CapabilityProfileResolution.runtime_geometry_reason'),
        track_results=track_results, confirmed_member_event_count=0, anonymous_event_count=6,
        spatial_association_executed=False, database_query_mode='ro + query_only',
        formal_behavior_writes=0, formal_behavior_rows_in_window=formal_count,
    )
    engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
    # Hash the new report and its local evidence notes; retain older report
    # directories unchanged rather than rewriting their historical conclusions.
    evidence_paths = [p for p in args.output.parent.iterdir() if p.is_file() and p.name != 'artifact_sha256.json']
    evidence_paths += [Path(__file__), ROOT/'scripts/run_dpi01e2_cam03_offline_member_binding.py',
                       ROOT/'test/test_cam03_profile_loading.py']
    evidence_paths += [p for p in (
        Path(config.raw_path) if config.raw_path else None,
        Path(config.capability_profile_path) if config.capability_profile_path else None,
        ROOT/'docs/worklogs/WORKLOG_20260907_cam03-profile-reuse-audit.md',
        ROOT/'docs/worklogs/WORKLOG_INDEX.md',
        ROOT/'reports/p6s_face_recording_geometry_20260819/event_701_geometry_probe.json',
        ROOT/'reports/p6s_device_fingerprint_20260819/scoped_profile_resolution.json',
        Path('D:/GJJ/work/retailvision_archive/20260826/duplicates/docs/reports/p6s/P6S_FACE_RECORDING_SPATIAL_GEOMETRY_VALIDATION.md'),
        Path('D:/GJJ/work/retailvision_archive/20260826/duplicates/docs/reports/p6s/P6S_CAPABILITY_PROFILE_REAL_DEVICE_VALIDATION.md'),
    ) if p is not None and p.is_file()]
    (args.output.parent/'artifact_sha256.json').write_text(
        json.dumps({str(p.resolve()): sha(p) for p in evidence_paths},indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'status':report['status'],'track_results':track_results,
                      'coverage_status':coverage.status,'confirmed':0,'current_history_count':len(histories)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
