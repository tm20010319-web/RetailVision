"""Safely exercise the deployed device-recognition receiver and inspect one event."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "http://127.0.0.1:8000/api/v1/event/pull"
WAIT_TIMEOUT_SECONDS = 30.0
WAIT_INTERVAL_SECONDS = 0.25
TERMINAL_PROFILE_STATUSES = frozenset({"succeeded", "failed", "skipped"})

# A valid 1x1 JPEG, kept inline so the smoke check has no OpenCV dependency.
SYNTHETIC_CAPTURE_DATA_URI = (
    "data:image/jpeg;base64,"
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/2wBDAQICAgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCOiiigD//Z"
)


@dataclass(frozen=True)
class EventState:
    canonical_event_status: str
    visit_count: int
    profile_status: str
    complete: bool


def parse_args():
    parser = argparse.ArgumentParser(description="Device recognition smoke check")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--member-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--event-id", required=True)
    return parser.parse_args()


def post_json(payload):
    request = Request(
        DEFAULT_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return str(response.status)
    except HTTPError as error:
        return str(error.code)
    except (URLError, OSError):
        return "unavailable"
    except Exception:
        return "unavailable"


def device_time():
    utc8 = timezone(timedelta(hours=8))
    return datetime.now(timezone.utc).astimezone(utc8).isoformat()


def profile_status_for_job(job, capture_path, capture_root_or_parent):
    """Return a terminal profile status only after receiver-capture verification."""
    status = getattr(job, "status", None)
    if status != "succeeded":
        return status if isinstance(status, str) and status else "not_started"

    receiver_capture = _verified_capture_path(capture_path, capture_root_or_parent)
    payload = getattr(job, "payload", None)
    result = getattr(job, "result", None)
    if (
        receiver_capture is None
        or not isinstance(payload, dict)
        or not isinstance(result, dict)
        or not _matches_receiver_capture(payload.get("crop_path"), receiver_capture)
        or not _matches_receiver_capture(result.get("crop_path"), receiver_capture)
    ):
        return "succeeded_capture_mismatch"
    return "succeeded_capture_verified"


def _verified_capture_path(capture_path, capture_root_or_parent):
    try:
        capture_root = Path(capture_root_or_parent).resolve()
        resolved_capture = Path(capture_path).resolve()
        resolved_capture.relative_to(capture_root)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    try:
        return resolved_capture if resolved_capture.is_file() else None
    except OSError:
        return None


def _matches_receiver_capture(candidate_path, receiver_capture):
    if not isinstance(candidate_path, str) or not candidate_path:
        return False
    try:
        return Path(candidate_path).resolve() == receiver_capture
    except (OSError, RuntimeError, ValueError):
        return False


def event_state_for_source(
    session_factory,
    device_id,
    source_event_id,
    member_id,
    capture_root,
    inbox_model,
    visit_model,
    job_model,
):
    """Read the latest event state through a new session owned by this call."""
    db = session_factory()
    try:
        inbox = (
            db.query(inbox_model)
            .filter(
                inbox_model.device_id == device_id,
                inbox_model.source == "push",
                inbox_model.source_event_id == source_event_id,
            )
            .order_by(inbox_model.received_at.desc())
            .first()
        )
        if inbox is None:
            return EventState("not_found", 0, "not_started", False)

        visit_count = (
            db.query(visit_model)
            .filter(visit_model.device_event_id == inbox.canonical_event_id)
            .count()
        )
        job = (
            db.query(job_model)
            .filter(
                job_model.event_id == inbox.canonical_event_id,
                job_model.member_id == member_id,
                job_model.task_type == "customer_profile",
            )
            .one_or_none()
        )
        job_status = getattr(job, "status", None) if job is not None else None
        profile_status = (
            profile_status_for_job(job, inbox.capture_path, capture_root)
            if job is not None
            else "not_started"
        )
        return EventState(
            inbox.status,
            visit_count,
            profile_status,
            inbox.status == "processed"
            and visit_count == 1
            and job_status in TERMINAL_PROFILE_STATUSES
            and (
                job_status != "succeeded"
                or profile_status == "succeeded_capture_verified"
            ),
        )
    finally:
        db.close()


def wait_for_event_state(
    read_state,
    *,
    timeout_seconds=WAIT_TIMEOUT_SECONDS,
    interval_seconds=WAIT_INTERVAL_SECONDS,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """Poll a fresh state reader until complete, returning the last observed state."""
    deadline = clock() + timeout_seconds
    while True:
        state = read_state()
        if state.complete:
            return state, False
        remaining = deadline - clock()
        if remaining <= 0:
            return state, True
        sleep(min(interval_seconds, remaining))


def _timeout_state(state):
    return EventState(
        "timeout_" + state.canonical_event_status,
        state.visit_count,
        "timeout_" + state.profile_status,
        False,
    )


def _print_statuses(
    heartbeat_status,
    face_reco_status,
    canonical_event_status,
    visit_count,
    profile_status,
):
    print(f"heartbeat_status={heartbeat_status}")
    print(f"face_reco_status={face_reco_status}")
    print(f"canonical_event_status={canonical_event_status}")
    print(f"visit_count={visit_count}")
    print(f"profile_status={profile_status}")


def main():
    args = parse_args()
    heartbeat_status = "not_sent"
    face_reco_status = "not_sent"
    canonical_event_status = "not_found"
    visit_count = 0
    profile_status = "not_started"

    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from agent.config import settings
        from agent.device_event_ingest import source_event_key
        from agent.models import (
            BusinessJob,
            DeviceMemberMapping,
            DeviceRecognitionInbox,
            Visit,
        )
    except Exception:
        _print_statuses(
            "unavailable_import",
            "unavailable_import",
            "unavailable_import",
            0,
            "unavailable_import",
        )
        return

    device_serial = getattr(settings, "P6S_DEVICE_SERIAL", "") or args.device_id
    heartbeat_status = post_json(
        {
            "operator": "heartbeat",
            "deviceInfo": {"serialNumber": device_serial},
            "info": {"eventId": args.event_id + "-heartbeat"},
        }
    )

    engine = None
    try:
        engine = create_engine(args.database_url)
        sessions = sessionmaker(bind=engine)
        db = sessions()
        try:
            mapping = (
                db.query(DeviceMemberMapping)
                .filter(
                    DeviceMemberMapping.device_id == args.device_id,
                    DeviceMemberMapping.member_id == args.member_id,
                    DeviceMemberMapping.sync_status == "synced",
                    DeviceMemberMapping.is_enabled.is_(True),
                )
                .one_or_none()
            )
        finally:
            db.close()

        if mapping is not None:
            event_time = device_time()
            face_reco_status = post_json(
                {
                    "operator": "FaceReco",
                    "deviceInfo": {"serialNumber": device_serial},
                    "info": {
                        "eventId": args.event_id,
                        "eventTime": event_time,
                        "matchNumber": 1,
                        "personInfo": {
                            "uniqueId": mapping.unique_id,
                            "groupId": mapping.group_id,
                            "personId": mapping.person_id,
                            "match": 100,
                        },
                        "CaptureImage": {"picture": SYNTHETIC_CAPTURE_DATA_URI},
                    },
                }
            )
            state, timed_out = wait_for_event_state(
                lambda: event_state_for_source(
                    sessions,
                    args.device_id,
                    source_event_key(
                        args.event_id, datetime.fromisoformat(event_time)
                    ),
                    args.member_id,
                    settings.P6S_CAPTURE_DIR,
                    DeviceRecognitionInbox,
                    Visit,
                    BusinessJob,
                )
            )
            if timed_out:
                state = _timeout_state(state)
            canonical_event_status = state.canonical_event_status
            visit_count = state.visit_count
            profile_status = state.profile_status
    except Exception:
        canonical_event_status = "unavailable"
        visit_count = 0
        profile_status = "unavailable"
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass

    _print_statuses(
        heartbeat_status,
        face_reco_status,
        canonical_event_status,
        visit_count,
        profile_status,
    )


if __name__ == "__main__":
    main()
