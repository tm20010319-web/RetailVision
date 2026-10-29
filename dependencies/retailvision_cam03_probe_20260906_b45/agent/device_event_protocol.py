"""Protocol contracts for replacement-camera push events."""

from datetime import datetime, timedelta, timezone


_EVENT_FLAGS = (
    "isEnableElectronicDefence",
    "isCrossBorderDetectEnable",
    "isOffDutyDetectEnable",
    "isPassengerFlowStaticsEnable",
    "isCryScreamDetectEnable",
    "isPetDetectEnable",
    "isFallDetectEnable",
    "isSnapshotEnable",
    "isPersonInfoEnable",
    "isPersonDetectEnable",
    "isCarLicenseSnapshotEnable",
    "isCarDetectEnable",
    "isTimedSnapshotEnable",
    "isGroundLockStatusChangedEnable",
    "isStartupReportEnable",
    "isAbnormalEventEnable",
    "isNonWhitelistCarEnable",
    "isMotionDetectEnable",
    "isTrafficStatisticsEnable",
    "isTrafficStatisticsCarShapeEnable",
    "isKey2CallEnable",
    "isLicensePlateSurveillanceEnable",
    "isFireDetectEventEnable",
    "isVideoCoverEventEnable",
    "isElectricBikeEventEnable",
    "isGasContainerEventEnable",
    "isElectronicDefenceV2EventEnable",
    "isPeopleNumberStatisticsEventEnable",
    "isPeopleNumberOverLimitEventEnable",
)
_UTC8 = timezone(timedelta(hours=8))


class ProtocolError(ValueError):
    """Raised when a device event cannot satisfy the vendor protocol."""


def _require_operator(payload: dict, expected: str) -> None:
    if not isinstance(payload, dict) or payload.get("operator") != expected:
        raise ProtocolError(f"operator must be {expected}")


def _require_envelope(payload: dict) -> tuple[str, object]:
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be an object")

    device_info = payload.get("deviceInfo")
    if not isinstance(device_info, dict) or not device_info.get("serialNumber"):
        raise ProtocolError("missing deviceInfo.serialNumber")

    info = payload.get("info")
    if not isinstance(info, dict) or info.get("eventId") is None:
        raise ProtocolError("missing info.eventId")

    return str(device_info["serialNumber"]), info["eventId"]


def event_key(payload: dict) -> str:
    """Return the vendor event key used by the diagnostic chain."""
    serial_number, event_id = _require_envelope(payload)
    return f"{serial_number}:{event_id}"


def heartbeat_ack(payload: dict, now: datetime) -> dict:
    """Build the heartbeat response expected by the camera."""
    _require_operator(payload, "heartbeat")
    _, event_id = _require_envelope(payload)
    ack_time = now.astimezone(_UTC8) if now.tzinfo is not None else now
    return {
        "operator": "heartbeat-Ack",
        "info": {
            "eventId": event_id,
            "time": ack_time.strftime("%Y%m%dT%H%M%S+08"),
            "eventSendMode": "realTime",
            "strategy": {
                "passengerStaticsInterval": 2,
                "heartBeatInterval": 30,
                **{flag: flag == "isPersonInfoEnable" for flag in _EVENT_FLAGS},
            },
        },
        "result": {"errorNo": 0, "description": "ok"},
    }


def _integer_or_zero(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def face_reco_ack(payload: dict) -> dict:
    """Build the minimal recognition acknowledgement."""
    _require_operator(payload, "FaceReco")
    _require_envelope(payload)

    info = payload["info"]
    person_info = info.get("personInfo")
    if not isinstance(person_info, dict):
        person_info = {}
    capture_image = info.get("CaptureImage")
    if not isinstance(capture_image, dict):
        capture_image = {}
    return {
        "operator": "FaceReco-Ack",
        "info": {
            "personId": _integer_or_zero(person_info.get("personId")),
            "uniqueId": str(person_info.get("uniqueId") or ""),
            "pictureMd5": str(capture_image.get("pictureMd5") or ""),
        },
        "result": {"errorNo": 0, "description": "ok"},
    }


def fallback_face_reco_ack() -> dict:
    return {
        "operator": "FaceReco-Ack",
        "info": {"personId": 0, "uniqueId": "", "pictureMd5": ""},
        "result": {"errorNo": 0, "description": "ok"},
    }


def unsupported_operator_ack() -> dict:
    return {
        "operator": "error-Ack",
        "result": {"errorNo": 1, "description": "unsupported_operator"},
    }
