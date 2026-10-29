"""The saved-Run CLI composes existing identity algorithms, not status mocks."""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from _test_bootstrap import ensure_project_root_on_path
ensure_project_root_on_path()
from scripts import run_cam03_run_identity as cli
from agent.offline_recognition_provider import RecognitionRecordQueryResult, OfflineRecognitionEvidence
from agent.offline_identity_association import OfflineSpatialGeometry
from agent.offline_identity import OfflineTrackIdentityAggregator, OfflineEventTrackAssociation
from agent.models import Base
from sqlalchemy import create_engine, text


def seal(directory):
    cli.write_json(directory/'artifact_sha256.json', {p.name:cli.sha(p) for p in directory.iterdir()
                                                   if p.name != 'artifact_sha256.json'})


@pytest.fixture
def setup_run(tmp_path, monkeypatch):
    root = tmp_path/'run'; root.mkdir()
    source = tmp_path/'video.mp4'; source.write_bytes(b'fixture-video')
    start = datetime(2026,9,7,3)
    stream = dict(time_base='1/90000',start_pts=0,duration_ts=1800000,width=2560,height=1440)
    monkeypatch.setattr(cli, 'source_timeline', lambda _: (stream, [i*90000 for i in range(20)],1800000))
    manifest = dict(camera_id='cam03',store_id='store-1',analysis_run_id='new-run',
        source_video=str(source),source_sha256=cli.sha(source),utc_start=start.isoformat(),
        utc_end=(start+timedelta(seconds=20)).isoformat(),duration_seconds=20,
        source_pts=stream, local_start='2026-09-07T11:00:00+08:00',time_source='fixture operator',raw_behavior_event_count=6)
    manifest['source_timeline_evidence']='source_timeline_evidence.json'
    cli.write_json(root/'source_timeline_evidence.json',dict(analysis_run_id='new-run',camera_id='cam03',
        source_sha256=manifest['source_sha256'],stream=stream,
        frames=[dict(source_seq=i,raw_pts=i*90000,duration_ticks=90000) for i in range(20)]))
    def common(seq):
        return dict(analysis_run_id='new-run',camera_id='cam03',source_seq=seq,raw_pts=seq*90000,
                    observed_at=(start+timedelta(seconds=seq)).isoformat(),source_pts_seconds=float(seq))
    people=[]; products=[]; events=[]
    for track,bbox in [(1,[.1,.1,.4,.9]),(2,[.6,.1,.9,.9])]:
        token=f'store-1:cam03:new-run:{track}'
        people.append(dict(**common(10),store_id='store-1',track_id=track,track_token=token,
            person_track_token=token,runtime_id='new-run',bbox=bbox,coordinate_space='normalized_full_frame',
            frame_size=[1280,720],source_frame_size=[2560,1440],analysis_frame_size=[1280,720],
            source_to_analysis_transform=dict(operation='resize',scale_x=.5,scale_y=.5,offset_x=0,offset_y=0,
                                              crop='none',padding='none',mirror=False,rotation=0),
            confidence=.95,role='customer',stable_frames=5,is_frame_clipped=False))
        product_token=f'product:cam03:new-run:product{track}_1'
        products.append(dict(**common(10), product_track_token=product_token))
        for kind,seq in [('PICK_UP',12),('HOLDING',12),('PUT_DOWN',16)]:
            events.append(dict(**common(seq),event_type=kind,event_id=f'e{track}-{kind}',person_track_id=track,
                person_track_token=token,product_key=f'product{track}',product_name='test guitar',
                product_track_token=product_token,interaction_session_id=f'session{track}',confidence=.9,
                holding_duration_seconds=4 if kind=='HOLDING' else 0))
    cli.write_json(root/'source_run_manifest.json',manifest)
    for name,rows in [('person_tracks.jsonl',people),('product_tracks.jsonl',products),('raw_stage9_events.jsonl',events)]:
        cli.write_jsonl(root/name,rows)
    seal(root)
    database = tmp_path/'test.db'
    engine=create_engine('sqlite:///'+str(database)); Base.metadata.create_all(engine); engine.dispose()
    args=SimpleNamespace(run_dir=root,output=tmp_path/'identity',database=database,
        camera_config=Path(cli.ROOT/'configs/behavior_cameras.cam03.identity.json'),diagnostic=True)
    monkeypatch.setattr(cli,'render_overlays',lambda *args: [])
    return args,manifest,events


def query(manifest, *, kind='confirmed'):
    start=datetime.fromisoformat(manifest['utc_start']); when=start+timedelta(seconds=10)
    evidence=OfflineRecognitionEvidence(evidence_id='e',canonical_event_id='canonical',device_id='cam03',
        occurred_at=when,member_id='member-test',similarity=.9,source='push',log_id=None,
        group_id=0,person_id=1,unique_id='fixture-id',face_spatial_evidence=dict(
            normalized_bbox=[.2,.15,.3,.3],source_frame_size=[1920,1088],quality_status='parsed',
            event_time=when.isoformat(),camera_id='cam03'))
    return RecognitionRecordQueryResult(status='unavailable' if kind=='failed' else 'complete',
        requested_start=start,requested_end=start+timedelta(seconds=20),
        accepted_evidence=() if kind=='empty' else (evidence,),record_audits=(),pages_fetched=1,
        raw_record_count=0 if kind=='empty' else 1, partial_record_count=1 if kind=='failed' else 0,
        coverage_complete=kind!='failed',failure_reason='page_failed' if kind=='failed' else None)


def runtime(result):
    return SimpleNamespace(provider=SimpleNamespace(query=Mock(return_value=result)),geometry=OfflineSpatialGeometry(
        decoded_source_frame_size=(1920,1088),analysis_frame_size=(1280,720),full_frame_resize_verified=True,
        cropped=False,mirrored=False,rotated=False,padded=False,transform_type='full_frame_resize',
        scale_x=1280/1920,scale_y=720/1088))


@pytest.mark.parametrize('kind,expected,count',[('confirmed',['confirmed','unmatched'],3),
    ('empty',['unmatched','unmatched'],0),('failed',['unavailable','unavailable'],0)])
def test_run_uses_guarded_chain_and_projector(setup_run,monkeypatch,kind,expected,count):
    args,manifest,events=setup_run
    instance=runtime(query(manifest,kind=kind))
    monkeypatch.setattr(cli,'configured_runtime',lambda *a:(instance,{}))
    report=cli.run(args)
    instance.provider.query.assert_called_once_with(datetime.fromisoformat(manifest['utc_start']),
                                                    datetime.fromisoformat(manifest['utc_end']))
    assert [t['status'] for t in report['track_results']]==expected
    assert report['confirmed_member_event_count']==count
    assert report['publication_eligible'] is False and report['formal_behavior_writes']==0
    annotated=[json.loads(line) for line in (args.output/'raw_behavior_identity_facts.jsonl').read_text().splitlines()]
    assert [r['raw_event'] for r in annotated]==events
    assert all(r['member_id'] is None for r in annotated[3:])


def test_normal_cli_loads_cam03_despite_cam02_environment_and_missing_coverage(setup_run,monkeypatch):
    args,_,_=setup_run
    monkeypatch.setenv('CAMERA_FACE_DEVICE_ID','cam02')
    monkeypatch.setenv('CAMERA_FACE_BASE_URL','http://obsolete-cam02')
    monkeypatch.setenv('RETAILVISION_CAMERA_CONFIG_PATH',str(args.camera_config))
    monkeypatch.setenv('BEHAVIOR_CONFIG_PATH','absent-cam02.json')
    monkeypatch.delenv('RETAILVISION_CAPABILITY_PROFILE_PATH',raising=False)
    before=cli.sha(args.database)
    assert cli.main(['--run-dir',str(args.run_dir),'--output',str(args.output),'--database',str(args.database),'--diagnostic'])==0
    report=cli.read_json(args.output/'identity_report.json')
    assert report['device_id']=='cam03' and report['evidence_mode']=='durable_push_inbox'
    assert report['runtime']['provider']=='DurablePushInboxRecognitionProvider'
    assert report['recognition_coverage_status']=='unavailable'
    assert {t['status'] for t in report['track_results']}=={'unavailable'}
    assert cli.sha(args.database)==before
    assert 'obsolete-cam02' not in json.dumps(report)


def test_missing_profile_without_diagnostic_does_not_query_provider(setup_run,monkeypatch):
    args,_,_=setup_run; args.diagnostic=False
    monkeypatch.delenv('RETAILVISION_CAPABILITY_PROFILE_PATH',raising=False)
    constructor=Mock(); monkeypatch.setattr(cli,'DurablePushInboxRecognitionProvider',constructor)
    with pytest.raises(ValueError,match='recognition_configuration_unavailable:recognition.capability_profile'):
        cli.run(args)
    constructor.assert_not_called()


def test_normal_cli_records_explicit_diagnostic_acceptance_threshold(setup_run, monkeypatch):
    args, _, _ = setup_run
    monkeypatch.delenv('RETAILVISION_CAPABILITY_PROFILE_PATH', raising=False)
    assert cli.main(['--run-dir', str(args.run_dir), '--output', str(args.output),
        '--database', str(args.database), '--camera-config', str(args.camera_config),
        '--diagnostic', '--diagnostic-similarity-threshold', '0.6']) == 0
    report = cli.read_json(args.output / 'identity_report.json')
    assert report['acceptance_policy']['similarity_threshold'] == '0.6'
    assert report['acceptance_policy']['policy_hash'] == 'v1:sha256:321fbdc550588f5f501b5666b06695d9259612d4411aabfe457bcf7b4c721b7e'
    assert report['confirmed_member_event_count'] == 0
    assert report['publication_eligible'] is False


def test_normal_cli_refuses_diagnostic_threshold_in_qualified_mode(setup_run):
    args, _, _ = setup_run
    with pytest.raises(ValueError, match='diagnostic_threshold_requires_diagnostic_mode'):
        cli.main(['--run-dir', str(args.run_dir), '--output', str(args.output),
            '--database', str(args.database), '--camera-config', str(args.camera_config),
            '--diagnostic-similarity-threshold', '0.6'])
    assert not args.output.exists()


def test_real_config_selects_existing_runtime_factory(setup_run,monkeypatch):
    args,_,_=setup_run
    config=replace(cli._formal_camera_configuration(config_path=args.camera_config),available=True,missing=())
    data=cli.load_run(args.run_dir,config)
    resolution=SimpleNamespace(status='resolved',reason=None,production_eligibility_reason=lambda **k:None)
    monkeypatch.setattr(cli,'resolve_configured_profile',lambda *a:resolution)
    built=SimpleNamespace(provider=Mock(),geometry=Mock())
    factory=Mock(return_value=built); monkeypatch.setattr(cli,'build_recognition_runtime',factory)
    with cli.readonly_sessions(args.database) as sessions:
        result,_=cli.configured_runtime(config,data,sessions,False)
    assert result is built
    assert factory.call_args.args[0] is config
    assert factory.call_args.kwargs['track_supplier'](data.request)==data.tracks


@pytest.mark.parametrize('status',['ambiguous','unavailable','probable'])
def test_nonconfirmed_existing_aggregation_cannot_project_a_member(setup_run,monkeypatch,status):
    args,manifest,_=setup_run
    instance=runtime(query(manifest,kind='empty'))
    monkeypatch.setattr(cli,'configured_runtime',lambda *a:(instance,{}))
    # Exercise real aggregator/projector with a controlled event-level outcome;
    # no test invents a confirmed Track by setting a string on a public fact.
    original=OfflineTrackIdentityAggregator.aggregate
    def aggregate(self,**kwargs):
        kwargs['associations']=(OfflineEventTrackAssociation(member_id=None,status=status,
            candidate_member_id='candidate' if status=='probable' else None),)
        return original(self,**kwargs)
    monkeypatch.setattr(OfflineTrackIdentityAggregator,'aggregate',aggregate)
    report=cli.run(args)
    assert {t['status'] for t in report['track_results']}=={status}
    assert report['confirmed_member_event_count']==0 and not report['publication_eligible']


@pytest.mark.parametrize('mutation,reason', [('camera','scope'),('token','token'),('pts','seq_pts'),('end','observation_time'),('sha','hash')])
def test_run_rejects_grafted_or_corrupt_evidence_before_provider(setup_run,monkeypatch,mutation,reason):
    args,_,_=setup_run
    path=args.run_dir/'person_tracks.jsonl'; rows=[json.loads(l) for l in path.read_text().splitlines()]
    if mutation=='camera': rows[0]['camera_id']='cam02'
    elif mutation=='token': rows[0]['track_token']='store-1:cam03:old-run:1'
    elif mutation=='pts': rows[0]['raw_pts']=10
    elif mutation=='end': rows[0]['observed_at']='2026-09-07T03:00:20'
    else: rows[0]['confidence']=.5
    cli.write_jsonl(path,rows)
    if mutation!='sha': seal(args.run_dir)
    factory=Mock(); monkeypatch.setattr(cli,'configured_runtime',factory)
    with pytest.raises(ValueError,match=reason): cli.run(args)
    factory.assert_not_called()


def test_readonly_connection_rejects_write(setup_run):
    args,_,_=setup_run
    with cli.readonly_sessions(args.database) as sessions, sessions() as session:
        with pytest.raises(Exception,match='readonly'):
            session.execute(text('CREATE TABLE forbidden_write (id INTEGER)'))


def test_complete_empty_real_inbox_is_unmatched_not_unavailable(setup_run,monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from agent.push_coverage import PushCoverageStore
    args,manifest,_=setup_run
    engine=create_engine('sqlite:///'+str(args.database))
    store=PushCoverageStore(sessionmaker(bind=engine,expire_on_commit=False))
    start=datetime.fromisoformat(manifest['utc_start'])
    common=dict(device_id='cam03',receiver_instance_id='test-receiver',boot_id='test-boot',outcome='success')
    store.record(**common,observation_type='receiver_started',observed_at=start-timedelta(seconds=2),observation_key='start')
    for seconds in (-1,21):
        for kind in ('heartbeat_accepted','persistence_success'):
            store.record(**common,observation_type=kind,observed_at=start+timedelta(seconds=seconds),
                         observation_key=f'{kind}:{seconds}',source_event_id=f'heartbeat:{seconds}')
    engine.dispose()
    monkeypatch.delenv('RETAILVISION_CAPABILITY_PROFILE_PATH',raising=False)
    before=cli.sha(args.database)
    report=cli.run(args)
    assert report['runtime']['push_coverage']['status']=='complete'
    assert report['recognition_coverage_status']=='complete'
    assert {t['status'] for t in report['track_results']}=={'unmatched'}
    assert report['confirmed_member_event_count']==0
    assert cli.sha(args.database)==before


def test_run_does_not_invent_verified_timeline(setup_run,monkeypatch):
    args,manifest,_=setup_run
    saved=cli.read_json(args.run_dir/'source_run_manifest.json')
    del saved['source_timeline_evidence']
    cli.write_json(args.run_dir/'source_run_manifest.json',saved)
    seal(args.run_dir)
    instance=runtime(query(manifest))
    monkeypatch.setattr(cli,'configured_runtime',lambda *a:(instance,{}))
    real_pass=cli.GuardedOfflineIdentityPass
    observed=[]
    def capture_pass(**kwargs):
        observed.append(kwargs['presentation_timeline_status'])
        return real_pass(**kwargs)
    monkeypatch.setattr(cli,'GuardedOfflineIdentityPass',capture_pass)
    report=cli.run(args)
    assert observed==['unavailable']
    assert report['confirmed_member_event_count']==0


def test_sampling_gaps_do_not_mean_pts_corruption(setup_run,monkeypatch):
    args,manifest,_=setup_run
    instance=runtime(query(manifest))
    monkeypatch.setattr(cli,'configured_runtime',lambda *a:(instance,{}))
    report=cli.run(args)
    assert report['timeline']['sampling_gap_count']>0
    assert report['timeline']['source_pts_status']=='available'
    assert report['timeline']['presentation_timeline_status']=='verified'
    assert report['timeline']['device_video_clock_status']=='unverified'


@pytest.mark.parametrize('duration,reason',[(None,'frame_duration_unavailable'),(100,'timeline_discontinuity')])
def test_invalid_actual_duration_cannot_use_saved_verified_status(setup_run,monkeypatch,duration,reason):
    args,manifest,_=setup_run
    saved=cli.read_json(args.run_dir/'source_timeline_evidence.json')
    saved['frames'][3]['duration_ticks']=duration
    saved['assessment']={'presentation_timeline_status':'verified'}
    cli.write_json(args.run_dir/'source_timeline_evidence.json',saved); seal(args.run_dir)
    monkeypatch.setattr(cli,'configured_runtime',lambda *a:(runtime(query(manifest)),{}))
    report=cli.run(args)
    assert report['timeline']['presentation_timeline_status']=='unavailable'
    assert report['timeline']['source_pts_status']=='available'
    assert report['timeline']['reason']==reason
    assert report['confirmed_member_event_count']==0


@pytest.mark.parametrize('threshold', [0.75, 0.6])
def test_existing_receiver_lifespan_no_migration_or_behavior_writes(setup_run,monkeypatch,threshold):
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from agent import db, member_api
    from agent.config import settings
    from scripts.run_cam03_push_receiver import prepare_receiver
    args,_,_=setup_run
    monkeypatch.setenv('RETAILVISION_CAMERA_CONFIG_PATH',str(args.camera_config))
    monkeypatch.setenv('CAMERA_FACE_DEVICE_ID','cam03')
    monkeypatch.setenv('P6S_DEVICE_SERIAL','fixture-cam03-serial')
    monkeypatch.setattr(settings,'P6S_DEVICE_SERIAL','fixture-cam03-serial')
    monkeypatch.setattr(settings,'CAMERA_FACE_DEVICE_ID','cam03')
    monkeypatch.setattr(settings,'P6S_SIMILARITY_THRESHOLD',threshold)
    engine=create_engine('sqlite:///'+str(args.database),connect_args={'check_same_thread':False})
    monkeypatch.setattr(db,'_engine',engine)
    monkeypatch.setattr(db,'_SessionLocal',sessionmaker(bind=engine,autoflush=False))
    monkeypatch.setattr(member_api,'_service_instance',None)
    original_init=member_api.init_db
    monkeypatch.setattr(member_api,'init_db',original_init)  # restore process-local override after test
    try:
        with TestClient(prepare_receiver()) as client:
            assert member_api.app.state.device_recognition_intake.similarity_threshold == threshold
            assert client.get('/openapi.json').status_code==200
            with engine.connect() as connection:
                with pytest.raises(Exception,match='not authorized'):
                    connection.exec_driver_sql('DELETE FROM behavior_events')
                with pytest.raises(Exception,match='not authorized'):
                    connection.exec_driver_sql('CREATE TABLE forbidden_new_table (id INTEGER)')
        with engine.connect() as connection:
            kinds={r[0] for r in connection.exec_driver_sql('SELECT observation_type FROM push_coverage_observations')}
            assert {'receiver_started','receiver_graceful_stopped'} <= kinds
    finally:
        engine.dispose()
