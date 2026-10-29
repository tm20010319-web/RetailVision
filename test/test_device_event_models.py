from test._test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent import models
from agent.schema_evolution import evolve_schema


def make_sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:")
    evolve_schema(engine)
    return sessionmaker(bind=engine)


class DeviceEventModelTests(unittest.TestCase):
    def test_event_inbox_status_defaults_to_received(self):
        inbox_model = getattr(models, "DeviceRecognitionInbox", None)
        self.assertIsNotNone(inbox_model)
        db = make_sqlite_session_factory()()
        self.addCleanup(db.get_bind().dispose)
        self.addCleanup(db.close)
        db.add(inbox_model(
            canonical_event_id="evt-1",
            device_id="entrance-1",
            source="push",
            source_event_id="camera-1",
            occurred_at=datetime(2026, 7, 20, 12, 0, 0),
        ))
        db.commit()
        self.assertEqual("received", db.get(inbox_model, "evt-1").status)


if __name__ == "__main__":
    unittest.main()
