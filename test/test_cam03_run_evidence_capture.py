import copy
import json
from types import SimpleNamespace
from fractions import Fraction

import pytest
from _test_bootstrap import ensure_project_root_on_path
ensure_project_root_on_path()
from scripts import run_cam03_evidence_capture as capture


def sample(run='new-run'):
    people = [dict(analysis_run_id=run, camera_id='cam03', track_id=i,
                   track_token=f'store-1:cam03:{run}:{i}', bbox=[.1,.1,.4,.8],
                   source_seq=20, raw_pts=90000, time_base_num=1, time_base_den=90000,
                   source_frame_size=[2560,1440], analysis_frame_size=[1280,720],
                   source_to_analysis_transform=dict(operation='resize',scale_x=.5,scale_y=.5))
              for i in (1,2)]
    events = [dict(analysis_run_id=run,camera_id='cam03',person_track_token=p['track_token'],
                   event_type='PICK_UP', source_pts_seconds=1, confidence=.85,
                   product_key=f'product{i}',product_track_token=f'product:cam03:{run}:{i}')
              for i,p in enumerate(people)]
    return SimpleNamespace(person_tracks=people, product_tracks=[], raw_stage9_events=events,
                           behavior_summary={}, analyzed_video_bytes=b'')


def manifest():
    return dict(analysis_run_id='new-run', store_id='store-1', duration_seconds=113.201)


def test_two_tracks_export_independently_preserving_event_facts(tmp_path):
    behavior = sample()
    before = copy.deepcopy(behavior.raw_stage9_events)
    capture.export_run(tmp_path, behavior, manifest())
    after = [json.loads(line) for line in (tmp_path/'raw_stage9_events.jsonl').read_text().splitlines()]
    assert after == before == behavior.raw_stage9_events
    people = [json.loads(line) for line in (tmp_path/'person_tracks.jsonl').read_text().splitlines()]
    assert len({r['person_track_token'] for r in people}) == 2
    assert people[0]['bbox'] == behavior.person_tracks[0]['bbox']
    assert json.loads((tmp_path/'artifact_sha256.json').read_text())['person_tracks.jsonl'] == capture.sha(tmp_path/'person_tracks.jsonl')


@pytest.mark.parametrize('mutation,reason', [
    ('old_run','same_run_evidence_scope_mismatch'),
    ('old_token','person_track_run_token_mismatch'),
    ('missing_bbox','person_observation_missing:bbox'),
    ('missing_snapshot','behavior_person_observation_missing'),
    ('end_boundary','behavior_outside_source_window'),
])
def test_no_old_run_snapshot_grafting(tmp_path, mutation, reason):
    behavior = sample()
    if mutation == 'old_run':
        behavior.person_tracks[0]['analysis_run_id'] = 'old-run'
    elif mutation == 'old_token':
        behavior.person_tracks[0]['track_token'] = 'store-1:cam03:old-run:1'
    elif mutation == 'missing_bbox':
        del behavior.person_tracks[0]['bbox']
    elif mutation == 'missing_snapshot':
        behavior.person_tracks.pop()
    else:
        behavior.raw_stage9_events[0]['source_pts_seconds'] = 113.201
    with pytest.raises(ValueError, match=reason):
        capture.export_run(tmp_path, behavior, manifest())
    assert not list(tmp_path.iterdir())


def test_source_pts_are_not_frame_count_times_time_base(monkeypatch):
    payload = dict(streams=[dict(time_base='1/90000',start_pts=0,duration_ts=10188090)],
                   frames=[dict(pts=p) for p in (0,3600,7200)])
    monkeypatch.setattr(capture.subprocess, 'check_output', lambda *a,**k: json.dumps(payload))
    stream, pts, end = capture.source_timeline('source.mp4')
    duration = Fraction(stream['time_base']) * (end-pts[0])
    assert float(duration) == 113.201
    start, stop = capture.confirmed_window('2026-09-06T19:11:00+08:00','operator + video overlay',duration)
    assert start.isoformat() == '2026-09-06T11:11:00'
    assert stop.isoformat() == '2026-09-06T11:12:53.201000'
    with pytest.raises(ValueError, match='Asia_Shanghai'):
        capture.confirmed_window('2026-09-06T19:11:00','no offset',duration)


def test_normal_entry_exports_same_run_without_database_or_model(monkeypatch,tmp_path):
    from pathlib import Path
    video = tmp_path/'video.mp4'; video.write_bytes(b'test source')
    config = tmp_path/'config.json'; config.write_text('{}')
    confirm = tmp_path/'confirmed.json'; confirm.write_text('{}')
    output = tmp_path/'new-run'
    monkeypatch.setattr(capture.sys, 'argv', [str(Path(capture.__file__)),
        '--video',str(video),'--output',str(output),'--analyzer-root',str(tmp_path),
        '--product-config',str(config),'--initialization-confirmation',str(confirm),
        '--initialization-dir',str(tmp_path),'--store-id','store-1',
        '--local-start','2026-09-07T11:00:00+08:00','--time-source','operator'])
    monkeypatch.setattr(capture, 'source_timeline', lambda _: (dict(time_base='1/90000',start_pts=0,duration_ts=180000),[0,90000],180000))
    monkeypatch.setattr(capture, 'source_presentation_frames', lambda _: [
        dict(source_seq=i,raw_pts=i*90000,duration_ticks=90000) for i in range(2)])
    calls = []
    def analyzer(args, run_id, *remaining):
        calls.append(run_id)
        return sample(run_id), {'test_analyzer':True}
    monkeypatch.setattr(capture,'run_analyzer',analyzer)
    assert capture.main() == 0
    assert len(calls) == 1 and calls[0].startswith('cam03-')
    report = json.loads((output/'source_run_manifest.json').read_text())
    assert report['formal_behavior_writes'] == 0
    assert report['identity_status'] == 'not_executed'
    with pytest.raises(ValueError, match='new_empty_run_directory_required'):
        capture.main()


def test_existing_analyzer_exports_measured_snapshot_fields():
    from scripts.run_offline_complete_loop_golden import _append_observations
    from datetime import datetime
    request = SimpleNamespace(analysis_run_id='new-run',store_id='store-1',camera_id='cam03')
    person = SimpleNamespace(track_id=1,track_token='store-1:cam03:new-run:1',role='customer',
                             confidence=.9,bbox=(0,.1,.4,.8),centroid=(.2,.45),foot_point=(.2,.8),
                             observed_at=datetime(2026,9,7))
    result = SimpleNamespace(persons=[person],products=[],context=SimpleNamespace(frame_width=1280,frame_height=720))
    rows=[]
    for seq in (0,1,3):
        _append_observations(rows, [], result, request, {'sample_seq':seq,'source_seq':seq*5,'raw_pts':seq*9000})
    assert [r['stable_frames'] for r in rows] == [1,2,1]
    assert all(r['frame_size']==[1280,720] and r['is_frame_clipped'] for r in rows)
    assert all(r['person_track_token']==person.track_token for r in rows)
