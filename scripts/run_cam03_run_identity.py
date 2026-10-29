"""Read-only identity CLI for one exported cam03 Run; never a publisher.

Use --diagnostic to inspect the existing durable inbox without a qualified
profile. That mode does not qualify geometry or bypass the publication gate.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from agent.camera_track_identity import SameCameraRecognitionBinder
from agent.face_spatial_evidence import FaceSpatialEvidence
from agent.models import DeviceMemberIdentityRevision, DeviceRecognitionInbox, PushCoverageObservation
from agent.offline_behavior import Stage9BehaviorFactAdapter, OfflineBehaviorIdentityProjector
from agent.offline_identity_association import GuardedOfflineIdentityPass, _coerce_track_snapshot
from agent.offline_identity_sql import SqlAlchemyHistoricalIdentityRepository
from agent.offline_recognition_provider import DeviceRecognitionAcceptancePolicy, DurablePushInboxRecognitionProvider
from agent.push_coverage import PushCoverageStore
from scripts.run_cam03_evidence_capture import sha, json_value, source_timeline, confirmed_window, assess_presentation_frames
from scripts.run_dpi01e2_cam03_offline_member_binding import (
    _formal_camera_configuration, resolve_configured_profile, build_recognition_runtime,
)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(json_value(r), ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def load_run(directory, config):
    """Verify source PTS and exact Run scope before any Recognition access."""
    hashes = read_json(directory / 'artifact_sha256.json')
    names = ('source_run_manifest.json', 'person_tracks.jsonl', 'product_tracks.jsonl', 'raw_stage9_events.jsonl')
    for name in names:
        if hashes.get(name) != sha(directory / name):
            raise ValueError('same_run_artifact_hash_mismatch:' + name)
    manifest = read_json(directory / names[0])
    if (manifest['camera_id'] != 'cam03' or config.camera_id != 'cam03'
            or config.device_id != 'cam03' or manifest['store_id'] != config.store_id):
        raise ValueError('same_run_camera_store_scope_mismatch')
    if sha(Path(manifest['source_video'])) != manifest['source_sha256']:
        raise ValueError('same_run_source_hash_mismatch')
    stream, pts, end_pts = source_timeline(Path(manifest['source_video']))
    base = Fraction(stream['time_base'])
    duration = base * (end_pts - pts[0])
    start, end = confirmed_window(manifest['local_start'], manifest['time_source'], duration)
    if (manifest['utc_start'] != start.isoformat() or manifest['utc_end'] != end.isoformat()
            or abs(manifest['duration_seconds'] - float(duration)) > 1e-6
            or manifest['source_pts'] != stream):
        raise ValueError('same_run_source_time_mismatch')
    timeline = dict(presentation_timeline_status='unavailable', source_pts_status='available',
                    reason='same_run_frame_duration_evidence_missing', sampling_gap_count=None)
    if manifest.get('source_timeline_evidence') == 'source_timeline_evidence.json':
        name = 'source_timeline_evidence.json'
        if hashes.get(name) != sha(directory/name):
            raise ValueError('same_run_timeline_evidence_hash_mismatch')
        saved = read_json(directory/name)
        if (saved['analysis_run_id'] != manifest['analysis_run_id'] or saved['camera_id'] != 'cam03'
                or saved['source_sha256'] != manifest['source_sha256'] or saved['stream'] != stream
                or [r['raw_pts'] for r in saved['frames']] != pts
                or [r['source_seq'] for r in saved['frames']] != list(range(len(pts)))):
            raise ValueError('same_run_timeline_evidence_scope_mismatch')
        # Recompute with existing rules; a saved 'verified' string is not proof.
        timeline = assess_presentation_frames(saved['frames'],stream)
    timeline['absolute_event_time_status']='available'
    timeline['absolute_time_basis']='declared_start_plus_original_pts'
    timeline['absolute_time_confirmation_source']=manifest['time_source']
    timeline['device_video_clock_status']=manifest.get('absolute_time_evidence',{}).get('device_video_clock_status','unverified')
    timeline['reported_sampling_gap_count']=manifest.get('behavior_summary',{}).get('sampling_gap_count')
    rows = [[json.loads(line) for line in (directory / name).read_text(encoding='utf-8').splitlines() if line]
            for name in names[1:]]
    people, products, events = rows
    if not people or len(events) != manifest['raw_behavior_event_count']:
        raise ValueError('same_run_evidence_missing')
    request = SimpleNamespace(analysis_job_id=manifest['analysis_run_id'],
                              analysis_run_id=manifest['analysis_run_id'], store_id=config.store_id, camera_id='cam03')
    for row in people + products + events:
        if row['analysis_run_id'] != request.analysis_run_id or row['camera_id'] != 'cam03':
            raise ValueError('same_run_evidence_scope_mismatch')
        seq = row['source_seq']
        if not isinstance(seq, int) or not 0 <= seq < len(pts) or row['raw_pts'] != pts[seq]:
            raise ValueError('same_run_source_seq_pts_mismatch')
        relative = float(base * (pts[seq] - pts[0]))
        at = datetime.fromisoformat(row['observed_at'])
        if (not start <= at < end or abs((at-start).total_seconds()-relative) > 1e-6
                or abs(row['source_pts_seconds']-relative) > 1e-6):
            raise ValueError('same_run_observation_time_mismatch')
    tokens = set()
    snapshots = []
    for row in people:
        expected = f"{config.store_id}:cam03:{request.analysis_run_id}:{row['track_id']}"
        if (row['track_token'] != expected or row['person_track_token'] != expected
                or row['runtime_id'] != request.analysis_run_id or row['store_id'] != config.store_id):
            raise ValueError('same_run_person_token_mismatch')
        if row['coordinate_space'] != 'normalized_full_frame' or row['source_frame_size'] != [stream['width'], stream['height']]:
            raise ValueError('same_run_coordinate_metadata_mismatch')
        bbox = row['bbox']
        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox) or not (0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1):
            raise ValueError('same_run_bbox_invalid')
        row_copy = {**row, 'observed_at': datetime.fromisoformat(row['observed_at'])}
        snapshot = _coerce_track_snapshot(row_copy, request)
        if snapshot is None or tuple(row['analysis_frame_size']) != snapshot.frame_size:
            raise ValueError('same_run_person_snapshot_invalid')
        snapshots.append(snapshot)
        tokens.add(expected)
    product_tokens = {p['product_track_token'] for p in products}
    for event in events:
        if event['person_track_token'] not in tokens or event['product_track_token'] not in product_tokens:
            raise ValueError('same_run_behavior_track_missing')
    return SimpleNamespace(manifest=manifest, people=people, events=events, tracks=tuple(snapshots),
                           request=request, start=start, end=end, timeline=timeline,
                           manifest_hash='v1:sha256:' + sha(directory / 'artifact_sha256.json'))


@contextmanager
def readonly_sessions(database):
    uri = database.resolve().as_uri() + '?mode=ro'
    def connect():
        connection = sqlite3.connect(uri, uri=True)
        connection.execute('PRAGMA query_only=ON')
        return connection
    engine = create_engine('sqlite://', creator=connect)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


def configured_runtime(config, run, sessions, diagnostic, diagnostic_similarity_threshold=None):
    # Both paths use the same parsed camera; no Settings camera defaults.
    if diagnostic_similarity_threshold is not None and not diagnostic:
        raise ValueError('diagnostic_threshold_requires_diagnostic_mode')
    with sessions() as session:
        earliest = session.query(func.min(DeviceMemberIdentityRevision.effective_from)).filter(
            DeviceMemberIdentityRevision.device_id == config.device_id).scalar()
        observation = session.query(PushCoverageObservation).filter(
            PushCoverageObservation.device_id == config.device_id,
            PushCoverageObservation.device_metadata.is_not(None),
        ).order_by(PushCoverageObservation.observed_at.desc()).first()
        metadata = observation.device_metadata if observation else {}
    resolution = resolve_configured_profile(config, metadata or {})
    reason = resolution.production_eligibility_reason(device_id=config.device_id)
    # No persisted history: the existing resolver must return unverifiable for
    # every timestamp in this Run, not crash or invent a bootstrap date.
    history = SqlAlchemyHistoricalIdentityRepository(sessions, history_available_from=earliest or datetime.max).resolver()
    coverage = PushCoverageStore(sessions)
    audit = dict(profile_status=resolution.status, profile_reason=resolution.reason or reason,
                 profile_id=config.capability_profile_id, configuration_missing=list(config.missing),
                 mode='diagnostic' if diagnostic else 'qualified_profile',
                 earliest_device_history=earliest, history_bound_status='persisted' if earliest else 'unavailable')
    if config.available and not reason:
        if diagnostic_similarity_threshold is not None:
            raise ValueError('diagnostic_threshold_cannot_override_qualified_profile')
        runtime = build_recognition_runtime(config, profile_resolution=resolution,
            historical_identity_resolver=history, track_supplier=lambda _: run.tracks,
            session_factory=sessions, push_coverage_store=coverage)
    else:
        non_profile_missing = [item for item in config.missing if not item.startswith('recognition.capability_profile:')]
        if non_profile_missing or not diagnostic or config.evidence_mode != 'durable_push_inbox':
            raise ValueError('recognition_configuration_unavailable:' + ','.join(config.missing or (reason,)))
        policy = DeviceRecognitionAcceptancePolicy.create(
            policy_id='device-recognition', policy_version='v1',
            similarity_threshold=diagnostic_similarity_threshold if diagnostic_similarity_threshold is not None else '0.75',
            accepted_rule_ids=(1,), accepted_pass_codes=(None, 0), require_reco_valid=False)
        runtime = SimpleNamespace(provider=DurablePushInboxRecognitionProvider(
            session_factory=sessions, push_coverage_store=coverage, historical_identity_resolver=history,
            acceptance_policy=policy, device_id=config.device_id, profile_resolution=None), geometry=None)
    audit.update(provider=type(runtime.provider).__name__, geometry_supplied=runtime.geometry is not None,
                 qualified_profile_available=config.available and not reason)
    audit['push_coverage'] = json_value(coverage.coverage(
        device_id=config.device_id, start_at=run.start, end_at=run.end)) if config.evidence_mode == 'durable_push_inbox' else None
    return runtime, audit


def associate_and_project(run, runtime):
    result = GuardedOfflineIdentityPass(provider=runtime.provider, track_supplier=lambda _: run.tracks,
        geometry=runtime.geometry, presentation_timeline_status=run.timeline['presentation_timeline_status'])(run.request, run.start, run.end)
    normalized = Stage9BehaviorFactAdapter().normalize(
        analysis_job_id=run.request.analysis_job_id, analysis_run_id=run.request.analysis_run_id,
        source_video_id=run.manifest['source_sha256'], raw_events=[
            {**r, 'observed_at': datetime.fromisoformat(r['observed_at'])} for r in run.events])
    projector = OfflineBehaviorIdentityProjector()
    projected = [projector.project(fact=fact, association=result.associations[fact.customer_track_token],
        enrichment=None, artifact_manifest_hash=run.manifest_hash,
        association_record_ref=fact.customer_track_token, store_id=run.request.store_id) for fact in normalized.facts]
    # Preserve the raw events verbatim/in order. The existing projector is the
    # ONLY source of member_id, including for these diagnostic annotations.
    candidates = {(f.interaction_session_id, p.event_type): p for f, p in zip(normalized.facts, projected)}
    names = {'PICK_UP':'merchandise_pick_up', 'HOLDING':'merchandise_holding', 'PUT_DOWN':'merchandise_put_down'}
    annotated = []
    for raw in run.events:
        candidate = candidates.get((raw['interaction_session_id'], names.get(raw['event_type'])))
        annotated.append(dict(raw_event=raw, identity_status=(candidate.identity_status if candidate else 'unavailable'),
                              member_id=(candidate.member_id if candidate else None), publication_eligible=False))
    return result, normalized, projected, annotated


def render_overlays(run, rows, output, geometry):
    """Illustrate the existing normalized-frame convention; not qualification.

    In unqualified mode no verified geometry or mapped Face object is invented.
    The original normalized bbox is drawn as a labelled resize hypothesis only.
    """
    import cv2
    from scripts.diagnose_event_804_spatial_identity import _rect
    from agent.offline_identity_association import map_face_bbox_to_recording_frame
    from agent.offline_identity_association import OfflineSpatialGeometryValidator
    frames = {}
    panels = []
    for row in rows[:3]:
        face = FaceSpatialEvidence.from_safe_dict(row.face_spatial_evidence)
        if face is None or face.camera_id not in {None, 'cam03'}:
            continue
        reference = face.occurred_at or row.occurred_at
        if not run.start <= reference < run.end:
            continue
        nearest = min(run.people, key=lambda p: abs((datetime.fromisoformat(p['observed_at'])-reference).total_seconds()))
        seqs = sorted({p['source_seq'] for p in run.people})
        index = seqs.index(nearest['source_seq'])
        for index2 in range(max(0,index-1), min(len(seqs),index+2)):
            seq = seqs[index2]
            bodies = [p for p in run.people if p['source_seq'] == seq]
            mapped = None
            if geometry and OfflineSpatialGeometryValidator().validate(face=face, geometry=geometry).status == 'compatible':
                mapped = map_face_bbox_to_recording_frame(face, geometry)
            panels.append((row, face, mapped, seq, bodies, reference, seq == nearest['source_seq']))
            frames[seq] = None
    if not panels:
        return []
    capture = cv2.VideoCapture(run.manifest['source_video'])
    try:
        # Decode by source_seq, not OpenCV FPS/PTS estimates or approximate seek.
        for seq in range(max(frames)+1):
            ok, frame = capture.read()
            if not ok:
                raise ValueError('overlay_source_decode_incomplete')
            if seq in frames:
                frames[seq] = cv2.resize(frame, (1280,720))
    finally:
        capture.release()
    audit = []
    for row, face, mapped, seq, bodies, reference, primary in panels:
        frame = frames[seq].copy()
        _rect(frame, (mapped or face).bbox, (0,215,255), 'INBOX FACE / ' + ('MAPPED' if mapped else 'RESIZE HYPOTHESIS'))
        body_diagnostics = []
        for body in bodies:
            _rect(frame, tuple(body['bbox']), (0,255,0), f"PERSON {body['track_id']}")
            snapshot = next(t for t in run.tracks if t.track_token == body['track_token']
                            and t.observed_at == datetime.fromisoformat(body['observed_at']))
            body_diagnostics.append(dict(track_token=body['track_token'],
                diagnostic=SameCameraRecognitionBinder()._spatial_diagnostic(mapped or face, snapshot, reference)))
        title = f"DIAGNOSTIC ONLY | seq {seq} | PTS {bodies[0]['source_pts_seconds']:.6f} | {'NEAREST' if primary else 'SUPPORT'}"
        cv2.putText(frame,title,(12,65),cv2.FONT_HERSHEY_SIMPLEX,.53,(0,255,255),1,cv2.LINE_AA)
        name = f"face_body_{len(audit):02d}_seq{seq}.jpg"
        if not cv2.imwrite(str(output / name),frame):
            raise OSError('overlay_write_failed')
        audit.append(dict(image=name, evidence_id=row.canonical_event_id, persisted_status=row.status,
            recognition_at=row.occurred_at, spatial_reference=reference, source_seq=seq,
            source_pts_seconds=bodies[0]['source_pts_seconds'], raw_pts=bodies[0]['raw_pts'],
            nearest_by_time=primary, face_source_size=face.source_frame_size,
            face_bbox=face.bbox, source_size=bodies[0]['source_frame_size'],
            analysis_size=bodies[0]['analysis_frame_size'], source_to_analysis_transform=bodies[0]['source_to_analysis_transform'],
            mapping_status='profile_mapped' if mapped else 'unqualified_full_frame_resize_hypothesis',
            qualification_changed=False, identity_evidence_used=False, body_diagnostics=body_diagnostics))
    return audit


def run(args):
    if args.output.exists():
        raise ValueError('new_identity_output_directory_required')
    config = _formal_camera_configuration(config_path=args.camera_config)
    data = load_run(args.run_dir, config)
    with readonly_sessions(args.database) as sessions:
        runtime, audit = configured_runtime(config, data, sessions, args.diagnostic,
            getattr(args, 'diagnostic_similarity_threshold', None))
        result, normalized, projected, annotated = associate_and_project(data, runtime)
        with sessions() as session:
            rows = session.query(DeviceRecognitionInbox).filter(
                DeviceRecognitionInbox.device_id == config.device_id, DeviceRecognitionInbox.source == 'push',
                DeviceRecognitionInbox.occurred_at >= data.start, DeviceRecognitionInbox.occurred_at < data.end,
            ).order_by(DeviceRecognitionInbox.occurred_at, DeviceRecognitionInbox.canonical_event_id).all()
        args.output.mkdir(parents=True, exist_ok=False)
        overlays = render_overlays(data, rows, args.output, runtime.geometry)
    write_jsonl(args.output / 'track_identity_associations.jsonl', result.associations.values())
    write_jsonl(args.output / 'raw_behavior_identity_facts.jsonl', annotated)
    write_jsonl(args.output / 'normalized_behavior_candidates.jsonl', projected)
    write_jsonl(args.output / 'recognition_record_audit.jsonl', result.record_audits)
    write_jsonl(args.output / 'recognition_page_audit.jsonl', result.page_audits)
    write_json(args.output / 'overlay_audit.json', overlays)
    report = dict(status='DIAGNOSTIC_COMPLETED' if args.diagnostic else 'ARTIFACT_ASSOCIATION_COMPLETED',
        cli_completed=True, camera_id='cam03', store_id=config.store_id, device_id=config.device_id,
        config_source=str(Path(config.raw_path).resolve()), config_sha256=sha(Path(config.raw_path)), evidence_mode=config.evidence_mode,
        runtime=audit, source_sha256=data.manifest['source_sha256'], analysis_run_id=data.request.analysis_run_id,
        timeline=data.timeline,
        input_manifest_hash=data.manifest_hash, source_run_directory=str(args.run_dir.resolve()),
        entry_sha256=sha(Path(__file__)), query_window=dict(start=data.start,end=data.end,interval='[start,end)'),
        recognition_query_executed=True, recognition_coverage_status=result.recognition_coverage_status,
        query_failure_reason=result.failure_reason, accepted_evidence_count=len(result.accepted_evidence),
        acceptance_policy=result.recognition_acceptance_policy_snapshot,
        track_results=[dict(person_track_token=t,status=a.status,member_id=a.member_id)
                       for t,a in sorted(result.associations.items())],
        raw_behavior_event_count=len(data.events), normalized_behavior_count=len(projected),
        normalization_diagnostics=normalized.diagnostics,
        behavior_track_tokens=sorted({e['person_track_token'] for e in data.events}),
        confirmed_member_event_count=sum(p.member_id is not None for p in projected),
        anonymous_event_count=sum(p.member_id is None for p in projected),
        overlay_count=len(overlays), alignment_status='diagnostic_images_require_review' if overlays else 'not_observed',
        database_mode='mode=ro + query_only', formal_behavior_writes=0,
        publication_attempted=False, publication_eligible=False,
        publication_gate_status='NOT_EVALUATED_ARTIFACT_ONLY', publication_gate_bypassed=False)
    write_json(args.output / 'identity_report.json', report)
    write_json(args.output / 'artifact_sha256.json', {p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--camera-config', type=Path)
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--diagnostic-similarity-threshold',
        help='Explicit unqualified diagnostic policy override; default remains 0.75.')
    args = parser.parse_args(argv)
    report = run(args)
    print(json.dumps({key:report[key] for key in ('status','cli_completed','recognition_coverage_status',
                     'confirmed_member_event_count','overlay_count')},ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
