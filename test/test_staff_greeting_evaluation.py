from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.behavior.behavior_events import BehaviorEventInput, BehaviorEventStore, EntryEventInput
from agent.behavior.behavior_monitor import BehaviorMonitorConfig, PolygonArea, TrackObservation
from agent.visits.customer_entry_visit import CustomerEntryObservation, CustomerEntryVisit
from agent.schema_evolution import evolve_schema
from agent.models import GreetingCycle, GreetingEvaluation
from agent.models import BehaviorEvent
from agent.behavior.staff_greeting_evaluation import GreetingShadowConfig, StaffGreetingEvaluation
from pipelines.behavior_runtime import BehaviorCameraRuntime


class StaffGreetingEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.events = BehaviorEventStore(self.sessions)
        self.entry_visits = CustomerEntryVisit(self.sessions)
        self.now = datetime(2026, 7, 31, 12, 0, 0)
        self.entry = EntryEventInput(
            entry_key="runtime-1:customer-4:entry:1",
            camera_id="entrance-1",
            track_id=4,
            track_token="store-1:entrance-1:runtime-1:4",
            occurred_at=self.now,
            confidence=0.95,
        )
        self.entry_row = self.entry_visits.confirm_entry(
            CustomerEntryObservation.from_behavior_entry(
                self.entry,
                store_id="store-1",
                runtime_session_id="runtime-1",
            )
        )

    def tearDown(self):
        self.engine.dispose()

    def test_runtime_persists_one_shadow_result_without_creating_official_behavior(self):
        area = PolygonArea(
            area_id="greeting-area",
            area_type="greeting",
            points=((0.1, 0.2), (0.9, 0.2), (0.9, 0.9), (0.1, 0.9)),
        )
        customer = TrackObservation(
            track_id=4, track_token=self.entry.track_token, role="customer",
            foot_point=(0.45, 0.55), world_point=(0.0, 0.0), person_height=0.30,
            confidence=0.95,
        )
        staff = TrackObservation(
            track_id=8, track_token="runtime-1:staff-8", role="staff",
            foot_point=(0.55, 0.55), world_point=(0.8, 0.0), person_height=0.30,
            confidence=0.94, role_source="staff_vest", role_confidence=0.96,
        )
        outside = TrackObservation(
            track_id=4, track_token=self.entry.track_token, role="customer",
            foot_point=(0.45, 0.1), world_point=(0.0, 0.0), person_height=0.30,
            confidence=0.95,
        )

        class Adapter:
            def __init__(self):
                self.index = 0
                self.last_observations = ()

            def process_frame(self, _frame):
                self.last_observations = (outside, staff) if self.index == 15 else (customer, staff)
                self.index += 1
                return list(self.last_observations)

        class Monitor:
            def __init__(self, event_store, entry, entry_visits):
                self.event_store = event_store
                self.entry = entry
                self.entry_visits = entry_visits
                self.index = 0

            def process(self, _observed_at, _observations):
                if self.index == 0:
                    self.entry_visits.confirm_entry(
                        CustomerEntryObservation.from_behavior_entry(
                            self.entry,
                            store_id="store-1",
                            runtime_session_id="runtime-1",
                        )
                    )
                    self.index += 1
                    return [self.entry], []
                self.index += 1
                return [], []

        runtime = BehaviorCameraRuntime(
            Adapter(),
            Monitor(self.events, self.entry, self.entry_visits),
            staff_greeting=StaffGreetingEvaluation(
                camera_id="entrance-1",
                entrance_area=None,
                greeting_area=area,
                config=BehaviorMonitorConfig(),
                behavior_sink=self.events.record_behavior,
                comparison_enabled=True,
                session_factory=self.sessions,
                shadow_config=GreetingShadowConfig(greeting_window_seconds=5.0),
            ),
        )
        for frame_index in range(16):
            runtime.process_frame(None, self.now + timedelta(seconds=frame_index / 10))

        db = self.sessions()
        try:
            self.assertEqual(1, db.query(GreetingCycle).count())
            shadow_evaluations = db.query(GreetingEvaluation).filter_by(mode="shadow").all()
            self.assertEqual(1, len(shadow_evaluations))
            self.assertEqual("uncertain", shadow_evaluations[0].decision)
            self.assertEqual(0, db.query(BehaviorEvent).count())
        finally:
            db.close()

    def test_official_persistence_failure_propagates(self):
        failure = RuntimeError("database unavailable")
        evaluation = StaffGreetingEvaluation(
            camera_id="entrance-1",
            entrance_area=None,
            greeting_area=None,
            config=BehaviorMonitorConfig(),
            behavior_sink=Mock(side_effect=failure),
        )
        event = BehaviorEventInput(
            event_key=f"{self.entry.entry_key}:greeting",
            event_type="staff_greeting",
            assessment="confirmed",
            camera_id=self.entry.camera_id,
            customer_track_id=self.entry.track_id,
            customer_track_token=self.entry.track_token,
            started_at=self.now,
            ended_at=self.now,
            confidence=0.9,
        )
        evaluation._official.observe = Mock(return_value=((event,), ()))

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            evaluation.observe(self.now, ())

    def test_shadow_failure_is_sanitized_and_does_not_block_official_evaluation(self):
        evaluation = StaffGreetingEvaluation(
            camera_id="entrance-1",
            entrance_area=None,
            greeting_area=None,
            config=BehaviorMonitorConfig(),
            behavior_sink=lambda _event: None,
            comparison_enabled=True,
            session_factory=self.sessions,
        )
        evaluation._shadow.observe = Mock(
            side_effect=RuntimeError("secret database details")
        )

        with self.assertLogs("agent.behavior.staff_greeting_evaluation", level="ERROR") as logs:
            result = evaluation.observe(self.now, ())

        self.assertEqual((), result)
        message = "\n".join(logs.output)
        self.assertIn("error_type=RuntimeError", message)
        self.assertNotIn("secret database details", message)


if __name__ == "__main__":
    unittest.main()
