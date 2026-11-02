"""Artifact-only cam03 entry over the retained two-guitar analysis adapter.

No identity assignment, database writer, threshold changes or product tuning.
The isolated analyzer root is explicit; its code hashes are retained per run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def json_value(value):
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat(timespec='microseconds')
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def source_timeline(video):
    payload = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
        '-show_entries', 'stream=time_base,start_pts,duration_ts,width,height,avg_frame_rate:frame=pts',
        '-of', 'json', str(video),
    ]))
    stream = payload['streams'][0]
    pts = [int(frame['pts']) for frame in payload['frames']]
    base = Fraction(stream['time_base'])
    if not pts or base <= 0 or any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError('source_presentation_timeline_invalid')
    end = int(stream['start_pts']) + int(stream['duration_ts'])
    if pts[0] != int(stream['start_pts']) or end <= pts[-1]:
        raise ValueError('source_presentation_boundary_invalid')
    return stream, pts, end


def source_presentation_frames(video):
    """Retain actual decoder durations; never infer them from FPS or PTS gaps."""
    payload = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
        '-show_entries', 'frame=pts,duration,pkt_duration', '-of', 'json', str(video),
    ]))
    return [dict(source_seq=i, raw_pts=int(f['pts']) if 'pts' in f else None,
                 duration_ticks=int(f.get('duration', f.get('pkt_duration', 0))) or None)
            for i, f in enumerate(payload['frames'])]


def assess_presentation_frames(rows, stream, *, analysis_fps=5.0):
    # Imported lazily: capture runs this only after selecting its retained
    # analyzer root; identity runs it with the current main-repository rules.
    from agent.offline.offline_media import AuthoritativePresentationTimeSampler, PresentationFrame
    frames = [PresentationFrame(**r) for r in rows]
    evidence = dict(presentation_timeline_status='unavailable', source_pts_status='unavailable',
                    sampling_gap_count=None, sampling_gaps=[], frame_count=len(frames),
                    duration_basis='ffprobe_frame_duration', reason=None)
    if frames and all(f.raw_pts is not None for f in frames) and all(
            b.raw_pts > a.raw_pts for a,b in zip(frames,frames[1:])):
        evidence['source_pts_status']='available'
    try:
        sampled = AuthoritativePresentationTimeSampler(analysis_fps=analysis_fps).sample(
            frames, presentation_origin_pts=int(stream['start_pts']), time_base=Fraction(stream['time_base']),
            presentation_continuity_verified=False)
        if frames[0].raw_pts != int(stream['start_pts']):
            raise ValueError('source_presentation_origin_mismatch')
        if frames[-1].raw_pts + frames[-1].duration_ticks != int(stream['start_pts']) + int(stream['duration_ts']):
            raise ValueError('source_presentation_end_mismatch')
        evidence.update(presentation_timeline_status='verified', source_pts_status='available',
                        sampling_gap_count=len(sampled.gaps), sampling_gaps=json_value(sampled.gaps),
                        sampled_frame_count=len(sampled.samples), media_duration_seconds=sampled.media_duration_seconds)
    except ValueError as error:
        evidence['reason']=str(error)
    return evidence


def confirmed_window(local_start, time_source, duration):
    if not time_source.strip():
        raise ValueError('recording_time_confirmation_source_required')
    local = datetime.fromisoformat(local_start)
    zone = ZoneInfo('Asia/Shanghai')
    if local.tzinfo is None or local.utcoffset() != local.astimezone(zone).utcoffset():
        raise ValueError('Asia_Shanghai_local_start_required')
    start = local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(seconds=float(duration))


def run_analyzer(args, run_id, start, stream, pts, end_pts):
    root = args.analyzer_root.resolve()
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != 'b45ed081ee41f5835568ee3b4facc6a97066444c':
        raise ValueError('retained_two_guitar_analyzer_revision_mismatch')
    sys.path.insert(0, str(root))
    # These are the EXISTING two-guitar adapter and configuration gates. The
    # main branch's generic Golden adapter does not implement its extensions.
    from scripts.probe_dpi01d_cam03_offline import _config_payload, _load_initialization_prompts
    from scripts import run_offline_complete_loop_golden as golden
    from agent.behavior.product_camera_config import load_dpi01c_product_camera_config, validate_formal_replay_readiness
    from agent.offline.offline_video_models import OfflineVideoAnalysisRequest
    from agent.behavior.product_first_frame_initialization import frame_sha256
    from tools.probe_dpi01c_two_guitars import _decode
    if Path(golden.__file__).resolve().parent.parent != root:
        raise ValueError('mixed_analyzer_import_roots')
    config = load_dpi01c_product_camera_config(args.product_config)
    if config.camera_id != 'cam03':
        raise ValueError('cam03_product_scope_required')
    confirmation = json.loads(args.initialization_confirmation.read_text(encoding='utf-8'))
    if confirmation.get('source_video_sha256') != sha(args.video) or confirmation.get('camera_id') != 'cam03':
        raise ValueError('initialization_source_mismatch')
    init_mapping = json.loads((args.initialization_dir/'initialization_mapping.json').read_text(encoding='utf-8'))
    actual_frame_hash = frame_sha256(_decode(args.video, 0))
    if (confirmation.get('source_seq') != 0 or init_mapping.get('source_seq') != 0
            or confirmation.get('frame_sha256') != actual_frame_hash
            or init_mapping.get('frame_sha256') != actual_frame_hash):
        raise ValueError('initialization_frame_mismatch')
    validate_formal_replay_readiness(config, confirmation.get('confirmations', confirmation.get('products', {})))
    product_config, surface, support_points = _config_payload(args.product_config, config)
    prompts = _load_initialization_prompts(args.initialization_dir)
    configured_store, _, association, interaction = golden._load_runtime_configs()
    if args.store_id != configured_store:
        raise ValueError('analysis_store_configuration_mismatch')
    if tuple(config.frame_size) != (association.frame_width, association.frame_height):
        raise ValueError('analysis_geometry_configuration_mismatch')
    # Feed original demux PTS to the EXISTING presentation-time sampler. The
    # old diagnostic reader used OpenCV's FPS-based ticks; it is not used here.
    def original_pts_reader(video, *, analysis_fps, presentation_continuity_verified):
        if Path(video).resolve() != args.video.resolve():
            raise ValueError('sampler_source_mismatch')
        frames = [golden.PresentationFrame(**row) for row in args.presentation_frames]
        if [f.raw_pts for f in frames] != pts:
            raise ValueError('presentation_probe_source_mismatch')
        base = Fraction(stream['time_base'])
        sampled = golden.AuthoritativePresentationTimeSampler(analysis_fps=analysis_fps).sample(
            frames, presentation_origin_pts=pts[0], time_base=base,
            presentation_continuity_verified=False,
        )
        return frames, sampled, base, float(Fraction(stream['avg_frame_rate']))
    golden._read_fixture_timeline = original_pts_reader
    request = OfflineVideoAnalysisRequest(
        analysis_job_id=run_id, analysis_run_id=run_id, video_path=args.video,
        store_id=args.store_id, camera_id='cam03', device_id='cam03',
        source_origin='untrusted_import', recording=None, controlled_download=False,
        timezone_offset_minutes=480, timezone_source=args.time_source,
        timezone_verified=False, analysis_fps=5.0,
    )
    analyzer = golden.GoldenMp4Analyzer(
        store_id=args.store_id, camera_id='cam03', association=association,
        interaction=interaction, sam_runtime_root=args.sam_runtime_root,
        candidate_assignments={key: int(prompt['obj_id']) for key, prompt in prompts.items()},
        selected_product_keys=config.selected_product_keys, product_config=product_config,
        product_surface=surface, display_evidence_mode=config.display_evidence_mode,
        support_contact_points=support_points, support_contact_tolerance=config.support_contact_tolerance,
        initialization_prompts=prompts, start_source_seq=0, diagnostics_root=args.output / 'diagnostics',
    )
    return analyzer(request, start), dict(
        analyzer_root=str(root), git_revision=revision,
        source_code_sha256={str(p.relative_to(root)): sha(p) for p in [
            root / 'scripts/probe_dpi01d_cam03_offline.py', Path(golden.__file__),
            root / 'agent/behavior/sam31_realtime_providers.py', root / 'agent/behavior/product_interaction.py',
        ]},
    )


def export_run(output, behavior, manifest):
    run_id = manifest['analysis_run_id']
    people = json_value(behavior.person_tracks)
    products = json_value(behavior.product_tracks)
    events = json_value(behavior.raw_stage9_events)
    for row in people + products + events:
        if row.get('analysis_run_id') != run_id or row.get('camera_id') != 'cam03':
            raise ValueError('same_run_evidence_scope_mismatch')
    tokens = set()
    for row in people:
        token = row['track_token']
        if token != f"{manifest['store_id']}:cam03:{run_id}:{row['track_id']}":
            raise ValueError('person_track_run_token_mismatch')
        row['person_track_token'] = token
        for key in ('source_seq', 'raw_pts', 'time_base_num', 'time_base_den', 'bbox',
                    'source_frame_size', 'analysis_frame_size', 'source_to_analysis_transform'):
            if key not in row:
                raise ValueError(f'person_observation_missing:{key}')
        tokens.add(token)
    if any(e.get('person_track_token') not in tokens for e in events):
        raise ValueError('behavior_person_observation_missing')
    if any(not 0 <= float(e['source_pts_seconds']) < manifest['duration_seconds'] for e in events):
        raise ValueError('behavior_outside_source_window')
    for name, rows in [('person_tracks.jsonl', people), ('product_tracks.jsonl', products),
                       ('raw_stage9_events.jsonl', events)]:
        (output / name).write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    manifest.update(person_observation_count=len(people), raw_behavior_event_count=len(events),
                    person_track_tokens=sorted(tokens), formal_behavior_writes=0,
                    identity_status='not_executed', recognition_geometry_qualified=False,
                    behavior_summary=json_value(behavior.behavior_summary))
    (output / 'source_run_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if behavior.analyzed_video_bytes:
        (output / 'sam3_behavior_overlay.mp4').write_bytes(behavior.analyzed_video_bytes)
    hashes = {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}
    (output / 'artifact_sha256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('video', 'output', 'analyzer-root', 'product-config', 'initialization-confirmation', 'initialization-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--store-id', required=True)
    parser.add_argument('--local-start', required=True)
    parser.add_argument('--time-source', required=True)
    parser.add_argument('--sam-runtime-root', type=Path, default=Path('D:/GJJ/work/sam3'))
    args = parser.parse_args()
    # Fail before inference if artifacts could overwrite another run.
    if args.output.exists():
        raise ValueError('new_empty_run_directory_required')
    stream, pts, end_pts = source_timeline(args.video)
    duration = Fraction(stream['time_base']) * (end_pts - pts[0])
    start, end = confirmed_window(args.local_start, args.time_source, duration)
    args.presentation_frames = source_presentation_frames(args.video)
    run_id = 'cam03-' + uuid.uuid4().hex
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = dict(analysis_run_id=run_id, camera_id='cam03', store_id=args.store_id,
                    source_video=str(args.video.resolve()), source_sha256=sha(args.video),
                    duration_seconds=float(duration), source_pts=stream,
                    utc_start=start.isoformat(), utc_end=end.isoformat(), interval='[start,end)',
                    local_start=args.local_start, timezone='Asia/Shanghai', time_source=args.time_source,
                    timeline_basis='ffprobe_original_pts_existing_sampler_v1',
                    entry_sha256=sha(Path(__file__)),
                    product_config_sha256=sha(args.product_config),
                    initialization_confirmation_sha256=sha(args.initialization_confirmation))
    behavior, provenance = run_analyzer(args, run_id, start, stream, pts, end_pts)
    manifest.update(provenance)
    timeline = dict(analysis_run_id=run_id, camera_id='cam03', source_sha256=manifest['source_sha256'],
                    stream=stream, frames=args.presentation_frames,
                    assessment=assess_presentation_frames(args.presentation_frames,stream))
    (args.output/'source_timeline_evidence.json').write_text(json.dumps(timeline,indent=2)+'\n',encoding='utf-8')
    manifest['source_timeline_evidence']='source_timeline_evidence.json'
    manifest['absolute_time_evidence'] = dict(status='operator_declared', source=args.time_source,
        local_start=args.local_start, timezone='Asia/Shanghai', device_video_clock_status='unverified')
    export_run(args.output, behavior, manifest)
    print(json.dumps({'run_id': run_id, 'output': str(args.output),
                      'person_observation_count': manifest['person_observation_count'],
                      'raw_behavior_event_count': manifest['raw_behavior_event_count']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
