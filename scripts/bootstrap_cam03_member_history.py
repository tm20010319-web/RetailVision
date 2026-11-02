"""Verify existing cam03 mappings on-device; optionally start history NOW only.

This queries face-group metadata, never uploads a face, changes a threshold,
assigns a Track, or publishes behavior. Credentials come only from the process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from agent.platform.camera_face_api import CameraFaceApiClient
from agent.identity.device_identity import SqlAlchemyDeviceIdentityRepository
from agent.models import DeviceMemberMapping, DeviceMemberIdentityRevision
from agent.offline.offline_identity_sql import SqlAlchemyHistoricalIdentityRepository
from scripts.run_dpi01e2_cam03_offline_member_binding import _formal_camera_configuration


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def complete_people(client, group_id, *, page_size=100):
    rows, pages, seen = [], [], set()
    for page in range(1, 101):
        values = client.query_person_info(group_id=group_id, page_size=page_size, page_num=page)
        digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        if values and digest in seen:
            raise ValueError('device_member_pagination_loop')
        seen.add(digest)
        pages.append(dict(group_id=group_id, page=page, count=len(values), response_sha256=digest))
        for row in values:
            if row.get('group_id') != group_id:
                raise ValueError('device_member_group_scope_mismatch')
        rows.extend(values)
        if len(values) < page_size:
            identities = [r.get('unique_id') or r.get('face_uuid') for r in rows]
            if not all(identities) or len(set(identities)) != len(identities):
                raise ValueError('duplicate_device_member_identity')
            return rows, pages
    raise ValueError('device_member_pagination_limit')


def verify_mapping(mapping, people):
    # Same fallback already used by MemberDeviceSyncService: the device's
    # FaceUUID, not a name, is the recognition UniqueID when this list omits it.
    matches = [p for p in people if (p.get('unique_id') or p.get('face_uuid')) == mapping.unique_id]
    if len(matches) != 1:
        return 'device_identity_not_unique_or_missing'
    person = matches[0]
    if (person.get('face_uuid') != mapping.face_uuid
            or person.get('person_id') != mapping.person_id
            or person.get('group_id') != mapping.group_id):
        return 'device_mapping_fields_mismatch'
    if str(person.get('model_state', '')).lower() != 'success':
        return 'device_model_not_ready'
    if not (mapping.is_enabled and mapping.sync_status == 'synced' and mapping.member_active):
        return 'current_mapping_not_eligible'
    return None


def run(args):
    if args.output.exists():
        raise ValueError('non_overwriting_output_required')
    config = _formal_camera_configuration(config_path=args.camera_config)
    if config.camera_id != 'cam03' or config.device_id != 'cam03' or not config.store_id:
        raise ValueError('cam03_scope_required')
    if config.evidence_mode != 'durable_push_inbox' or not config.base_url:
        raise ValueError('cam03_configuration_invalid')
    names = config.credential_env_names
    username = os.getenv(names['username'], '')
    # An explicitly configured empty password is valid for this P6S client.
    password = os.getenv(names['password'], '')
    serial = os.getenv(names['unique_code'], '')
    if not username or not serial:
        raise ValueError('device_query_requires_username_and_unique_code_environment')
    client = CameraFaceApiClient(device_id=config.device_id, base_url=config.base_url,
                                username=username, password=password, unique_code=serial,
                                max_retries=0)
    db_path = args.database.resolve()
    if not db_path.is_file():
        raise ValueError('existing_database_required')
    mode = 'rw' if args.apply_current else 'ro'
    def connect():
        connection = sqlite3.connect(f'{db_path.as_uri()}?mode={mode}', uri=True)
        if not args.apply_current:
            connection.execute('PRAGMA query_only=ON')
        return connection
    engine = create_engine('sqlite://', creator=connect)
    def guard(_connection, _cursor, sql, *_args):
        lowered = sql.strip().lower()
        if lowered.startswith(('insert ', 'update ', 'delete ', 'create ', 'alter ', 'drop ')):
            if not lowered.startswith(('insert into device_member_', 'update device_member_',
                                       'delete from device_member_')):
                raise ValueError('non_identity_database_write_forbidden')
    event.listen(engine, 'before_cursor_execute', guard)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyDeviceIdentityRepository(sessions)
    report = dict(camera_id=config.camera_id, device_id=config.device_id, store_id=config.store_id,
                  config_path=config.raw_path, apply_current=args.apply_current,
                  old_video_history_restored=False, formal_behavior_writes=0,
                  pages=[], results=[], status='BLOCKED', checked_at=utc_now().isoformat())
    try:
        with sessions() as session:
            ids = [r.member_id for r in session.query(DeviceMemberMapping).filter_by(
                device_id='cam03', sync_status='synced', is_enabled=True).all()]
        mappings = [repository.get_mapping('cam03', member_id) for member_id in ids]
        # Group UUID + device identity/face UUID are checked against the current
        # device response, not against an alias or a person's displayed name.
        groups = client.query_face_groups()
        for group_id in sorted({m.group_id for m in mappings}):
            group = [g for g in groups if g['group_id'] == group_id]
            expected = {m.group_uuid for m in mappings if m.group_id == group_id}
            if len(group) != 1 or expected != {group[0]['group_uuid']}:
                raise ValueError('device_group_uuid_mismatch')
            people, pages = complete_people(client, group_id)
            report['pages'].extend(pages)
            for mapping in (m for m in mappings if m.group_id == group_id):
                reason = verify_mapping(mapping, people)
                result = dict(member_id=mapping.member_id, status='unavailable' if reason else 'verified_current',
                              reason=reason, device_identity_sha256=hashlib.sha256(mapping.unique_id.encode()).hexdigest())
                report['results'].append(result)
                if reason or not args.apply_current:
                    continue
                # Preserve existing history and make this command idempotent.
                with sessions() as session:
                    existing = session.query(DeviceMemberIdentityRevision).filter_by(
                        device_id='cam03', unique_id=mapping.unique_id, effective_to=None).all()
                if existing:
                    if len(existing) != 1 or existing[0].member_id != mapping.member_id:
                        result.update(status='unavailable', reason='existing_history_conflict')
                        continue
                    prior = existing[0]
                    if (prior.mapping_enabled == mapping.is_enabled
                            and prior.mapping_sync_status == mapping.sync_status
                            and prior.member_principal_exists
                            and prior.member_principal_active == mapping.member_active):
                        result.update(status='history_already_present', effective_from=existing[0].effective_from.isoformat())
                        continue
                effective = utc_now()  # No --effective-from option; no backdating.
                history = SqlAlchemyHistoricalIdentityRepository(sessions, history_available_from=effective)
                history.apply_mapping_revision(mapping, effective_from=effective)
                result.update(status='history_started_now', effective_from=effective.isoformat())
        report['status'] = ('CURRENT_MAPPING_READY' if report['results'] and all(
            r['status'] != 'unavailable' for r in report['results']) else 'BLOCKED')
    except Exception as exc:
        # No response bodies, credentials, aliases, URLs or raw face payloads.
        report['failure_type'] = type(exc).__name__
        report['reason'] = str(exc) if isinstance(exc, ValueError) else 'device_or_database_check_failed'
    finally:
        engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'output': str(args.output),
                      'results': report['results'], 'failure_type': report.get('failure_type')}))
    return 0 if report['status'] == 'CURRENT_MAPPING_READY' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera-config', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply-current', action='store_true')
    raise SystemExit(run(parser.parse_args()))
