"""Compatibility imports for the replacement-camera diagnostic package."""

from agent.device_event_protocol import (
    ProtocolError,
    event_key,
    face_reco_ack,
    heartbeat_ack,
)

__all__ = ["ProtocolError", "event_key", "face_reco_ack", "heartbeat_ack"]
