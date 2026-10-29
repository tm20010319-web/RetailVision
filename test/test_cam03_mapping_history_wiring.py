"""The real sync path persists current-time revisions, never backdated history."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from _test_bootstrap import ensure_project_root_on_path
ensure_project_root_on_path()

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from agent.device_identity import DeviceMemberMappingData, SqlAlchemyDeviceIdentityRepository
from agent.member_device_sync import MemberDeviceSyncService
from agent.models import Member, DeviceMemberMapping, DeviceMemberIdentityRevision
from agent.offline_identity_sql import SqlAlchemyHistoricalIdentityRepository


@pytest.fixture
def wired():
    engine = create_engine('sqlite://')
    for model in (Member, DeviceMemberMapping, DeviceMemberIdentityRevision):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        session.add(Member(id='m1', name='Test', is_active=True))
        session.commit()
    statements = []
    event.listen(engine, 'before_cursor_execute', lambda c, cur, sql, *a: statements.append(sql))
    now = [datetime(2026, 9, 7, 3)]
    history = SqlAlchemyHistoricalIdentityRepository(sessions, history_available_from=now[0])
    repository = SqlAlchemyDeviceIdentityRepository(
        sessions, historical_revision_repository=history, utc_clock=lambda: now[0],
    )
    yield SimpleNamespace(sessions=sessions, history=history, repository=repository,
                          now=now, statements=statements)
    assert not any('behavior_events' in sql.lower() for sql in statements)
    engine.dispose()


def resolve(wired, when, identity='vendor1'):
    return wired.history.resolver().resolve_at(
        device_id='cam03', group_id=0, person_id=1, unique_id=identity, occurred_at=when,
    )


def mapping(**changes):
    fields = dict(device_id='cam03', member_id='m1', group_id=0, group_uuid='group',
                  person_id=1, unique_id='vendor1', face_uuid='face', sync_status='synced',
                  last_sync_at=datetime(2026, 9, 1))
    return DeviceMemberMappingData(**(fields | changes))


def test_current_time_not_last_sync_and_revoke_boundary(wired):
    wired.repository.save_mapping(mapping())
    start = wired.now[0]
    assert resolve(wired, datetime(2026, 9, 6, 11, 11, 40)).status == 'unverifiable'
    assert resolve(wired, start).member_id == 'm1'
    wired.now[0] += timedelta(seconds=1)
    wired.repository.save_mapping(mapping(sync_status='disabled', is_enabled=False))
    assert resolve(wired, start).status == 'resolved'
    assert resolve(wired, wired.now[0]).status == 'unmapped'


def test_replaced_device_identity_does_not_remain_open(wired):
    wired.repository.save_mapping(mapping())
    old = wired.now[0]
    wired.now[0] += timedelta(seconds=1)
    wired.repository.save_mapping(mapping(unique_id='vendor2', person_id=2))
    assert resolve(wired, old).status == 'resolved'
    assert resolve(wired, wired.now[0]).status == 'unmapped'
    assert resolve(wired, wired.now[0], 'vendor2').status == 'resolved'


def test_revision_failure_does_not_update_current_projection(wired):
    wired.repository.save_mapping(mapping())
    with pytest.raises(Exception, match='non_monotonic'):
        wired.repository.save_mapping(mapping(is_enabled=False))
    assert wired.repository.get_mapping('cam03', 'm1').is_enabled
    assert len(wired.history.load_revisions()) == 1


def test_real_sync_pending_then_confirmed_writes_history(wired):
    client = Mock(device_id='cam03')
    client.query_person_info.return_value = [dict(
        alias='test-alias', face_uuid='face', model_state='success',
        person_id=1, unique_id='vendor1',
    )]
    service = MemberDeviceSyncService(client=client, repository=wired.repository)
    service.authorize_member(member_id='m1', photo_bytes=b'authorized-test-photo',
                             declared_sex='male', group_id=0, group_uuid='group',
                             alias='test-alias', face_uuid='face')
    assert len(wired.history.load_revisions()) == 1
    assert resolve(wired, wired.now[0]).member_id == 'm1'
    client.update_person_and_face_image.assert_called_once()


def test_member_api_factory_enables_revision_repository(wired):
    from agent import member_api
    settings = SimpleNamespace(CAMERA_FACE_GROUP_UUID='group', CAMERA_FACE_GROUP_ID=0,
                               CAMERA_FACE_DEVICE_ID='cam03', CAMERA_FACE_BASE_URL='http://device')
    with patch.object(member_api, 'settings', settings), \
         patch.object(member_api, 'init_db'), \
         patch.object(member_api, 'get_session_factory', return_value=wired.sessions), \
         patch.object(member_api.CameraFaceApiClient, 'from_settings'), \
         patch.object(member_api, 'MemberManagementService') as service:
        member_api._build_service()
    sync = service.call_args.kwargs['device_sync_service']
    assert isinstance(sync.repository.historical_revision_repository,
                      SqlAlchemyHistoricalIdentityRepository)


def test_current_device_response_without_unique_id_uses_existing_face_uuid_contract():
    from scripts.bootstrap_cam03_member_history import complete_people, verify_mapping
    client = Mock()
    client.query_person_info.return_value = [
        dict(unique_id='', face_uuid=face, person_id=0, group_id=0, model_state='success')
        for face in ('face1', 'face2', 'face3')
    ]
    people, pages = complete_people(client, 0)
    assert len(people) == 3
    assert verify_mapping(mapping(unique_id='face1', face_uuid='face1', person_id=0), people) is None
    assert verify_mapping(mapping(unique_id='unrelated', face_uuid='face1', person_id=0), people)


def test_duplicate_face_identity_and_repeated_pages_are_rejected():
    from scripts.bootstrap_cam03_member_history import complete_people
    client = Mock()
    row = dict(unique_id='', face_uuid='face', person_id=0, group_id=0, model_state='success')
    client.query_person_info.return_value = [row, row]
    with pytest.raises(ValueError, match='duplicate_device_member_identity'):
        complete_people(client, 0)
    client.query_person_info.return_value = [row]
    with pytest.raises(ValueError, match='pagination_loop'):
        complete_people(client, 0, page_size=1)
