"""Start the existing member API receiver on an already prepared database.

No migrations, behavior workers, identity reassignment or threshold override.
Only reception/health/coverage writes are allowed in this process.
"""
import os
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def prepare_receiver():
    from sqlalchemy import event, inspect
    from scripts.run_dpi01e2_cam03_offline_member_binding import _formal_camera_configuration
    config = _formal_camera_configuration()
    if config.camera_id != 'cam03' or config.device_id != 'cam03' or config.evidence_mode != 'durable_push_inbox':
        raise ValueError('cam03_push_configuration_required')
    if os.getenv('CAMERA_FACE_DEVICE_ID') != config.device_id or not os.getenv('P6S_DEVICE_SERIAL'):
        raise ValueError('cam03_process_device_and_serial_required')
    from agent import db, member_api
    from agent.models import Base
    from agent.config import settings
    if settings.CAMERA_FACE_DEVICE_ID != config.device_id or settings.P6S_DEVICE_SERIAL != os.getenv('P6S_DEVICE_SERIAL'):
        raise ValueError('settings_must_be_loaded_after_cam03_process_environment')
    threshold = settings.P6S_SIMILARITY_THRESHOLD
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('similarity_threshold_invalid')
    print(f'cam03 receiver effective similarity threshold: {threshold:g}', flush=True)
    engine = db._get_engine()
    if engine.dialect.name != 'sqlite':
        raise ValueError('onsite_existing_sqlite_required')
    allowed = {'device_recognition_inbox', 'device_health_states', 'push_coverage_observations'}
    def authorize(action, table, _column, _database, _trigger):
        if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE} and table not in allowed:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    @event.listens_for(engine, 'checkout')
    def restrict(connection, _record, _proxy):
        connection.set_authorizer(authorize)
    def schema_ready_only():
        inspector = inspect(engine)
        for name in ('members','device_member_mappings','device_member_identity_revisions',
                     'device_recognition_inbox','push_coverage_observations','device_health_states'):
            table = Base.metadata.tables.get(name)
            if table is None:
                continue
            actual = {c['name'] for c in inspector.get_columns(name)} if inspector.has_table(name) else set()
            if set(table.columns.keys()) - actual:
                raise ValueError('existing_receiver_schema_incomplete:' + name)
    schema_ready_only()
    # Scoped to this child process. Retain the application's actual lifespan,
    # router, intake and history wiring; avoid its normally automatic migration.
    member_api.init_db = schema_ready_only
    return member_api.app


def main():
    app = prepare_receiver()
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8081, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
