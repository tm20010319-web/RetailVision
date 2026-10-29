"""Read-only threshold counterfactual; never a production qualification or publisher."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from agent.models import DeviceRecognitionInbox, DeviceMemberIdentityRevision
from agent.offline_identity_sql import SqlAlchemyHistoricalIdentityRepository
from agent.offline_recognition_provider import (
    DeviceRecognitionAcceptancePolicy, DurablePushInboxRecognitionProvider,
)
from agent.push_coverage import PushCoverageStore


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    source = Path('D:/GJJ/work/video/2026_09_06_19_16_00.mp4')
    replay_path = ROOT / 'reports/cam03_20260906_behavior_validation/replay/full_replay.json'
    out = ROOT / 'reports/cam03_20260907_threshold_experiment'
    if out.exists():
        raise RuntimeError('non_overwriting_experiment_output_required')
    replay = json.loads(replay_path.read_text(encoding='utf-8'))
    source_hash = sha(source)
    if source_hash != '48c1cdbb5d4e5da2c77add261859f5304b8517a302abf3b23f23422fd976051d':
        raise RuntimeError('source_mismatch')
    if replay['source_sha256'] != source_hash or len(replay['raw_stage9_events']) != 6:
        raise RuntimeError('replay_mismatch')
    probed = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=time_base,duration_ts', '-of', 'json', str(source),
    ]))['streams'][0]
    duration = Fraction(probed['time_base']) * int(probed['duration_ts'])
    if abs(float(duration) - replay['behavior_summary']['media_duration_seconds']) > 0.001:
        raise RuntimeError('duration_mismatch')
    local = datetime(2026, 9, 6, 19, 11, tzinfo=ZoneInfo('Asia/Shanghai'))
    start = local.astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(seconds=float(duration))

    def connect_read_only():
        conn = sqlite3.connect('file:D:/GJJ/work/code/demo_flow.db?mode=ro', uri=True)
        conn.execute('PRAGMA query_only=ON')
        return conn

    engine = create_engine('sqlite://', creator=connect_read_only)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as session:
        earliest = session.query(func.min(DeviceMemberIdentityRevision.effective_from)).scalar()
        if earliest is None:
            raise RuntimeError('no_persisted_history_bound')
        history_count = session.query(DeviceMemberIdentityRevision).filter_by(device_id='cam03').count()
        rows = session.query(DeviceRecognitionInbox).filter(
            DeviceRecognitionInbox.device_id == 'cam03',
            DeviceRecognitionInbox.source == 'push',
            DeviceRecognitionInbox.occurred_at >= start,
            DeviceRecognitionInbox.occurred_at < end,
        ).order_by(DeviceRecognitionInbox.occurred_at).all()
    # Earliest persisted revision is a diagnostic lower bound, not a claim that
    # all device histories are bootstrapped. In particular, cam03 has no history.
    resolver = SqlAlchemyHistoricalIdentityRepository(
        sessions, history_available_from=earliest,
    ).resolver()
    coverage_store = PushCoverageStore(sessions)
    trials = []
    for threshold in ('0.75', '0.65'):
        policy = DeviceRecognitionAcceptancePolicy.create(
            policy_id='cam03-threshold-counterfactual', policy_version='experiment-v1',
            similarity_threshold=threshold, accepted_rule_ids=(1,),
            accepted_pass_codes=(None, 0), require_reco_valid=False,
        )
        assessments = [asdict(policy.assess({
            'reco_valid': getattr(row, 'reco_valid', None),
            'pass_code': getattr(row, 'pass_code', None),
            'rule_id': row.rule_id, 'similarity': row.similarity,
        })) for row in rows]
        # Existing unqualified diagnostic mode only. Do not invent a verified
        # capability profile or update the persisted ignored status.
        provider = DurablePushInboxRecognitionProvider(
            session_factory=sessions, push_coverage_store=coverage_store,
            historical_identity_resolver=resolver, acceptance_policy=policy,
            device_id='cam03', profile_resolution=None,
        )
        result = provider.query(start, end)
        trials.append({
            'policy': asdict(policy), 'similarity_assessments': assessments,
            'provider_status': result.status, 'failure_reason': result.failure_reason,
            'coverage_complete': result.coverage_complete,
            'raw_record_count': result.raw_record_count,
            'accepted_evidence_count': len(result.accepted_evidence),
            'record_audits': [asdict(row) for row in result.record_audits],
            'same_camera_spatial_association_executed': False,
            'spatial_stop_reason': ('provider_failed_closed' if result.status != 'complete'
                                    else 'no_accepted_evidence'),
        })
    if any(t['accepted_evidence_count'] for t in trials):
        raise RuntimeError('new_evidence_requires_same_run_snapshot_and_geometry_review')
    report = {
        'status': 'EXPERIMENT_COMPLETED_IDENTITY_UNAVAILABLE',
        'experiment_only': True, 'production_qualified': False,
        'manual_identity_input_used': False, 'database_mode': 'ro + query_only',
        'database_write_performed': False, 'publication_attempted': False,
        'source_sha256': source_hash, 'replay_sha256': sha(replay_path),
        'script_sha256': sha(Path(__file__)), 'camera_id': 'cam03',
        'query_window': {'start': start.isoformat(), 'end': end.isoformat(),
                         'interval': '[start,end)', 'timezone': 'Asia/Shanghai',
                         'time_origin': 'previous_video_overlay_reading',
                         'source_stream': probed},
        'cam03_historical_revision_count': history_count,
        'history_diagnostic_lower_bound': earliest.isoformat(),
        'same_run_person_snapshots_exported': 'person_tracks' in replay,
        'production_capability_profile_supplied': False,
        'recognition_rows': [{'evidence_id': row.canonical_event_id,
                              'occurred_at': row.occurred_at.isoformat(),
                              'similarity': row.similarity, 'persisted_status': row.status,
                              'member_id_present': bool(row.member_id)} for row in rows],
        'trials': trials, 'confirmed_member_event_count': 0,
        'behavior_event_count_unchanged': len(replay['raw_stage9_events']),
    }
    engine.dispose()
    out.mkdir(parents=True)
    target = out / 'threshold_experiment.json'
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str)+'\n', encoding='utf-8')
    (out / 'artifact_sha256.json').write_text(json.dumps({target.name: sha(target)}, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
