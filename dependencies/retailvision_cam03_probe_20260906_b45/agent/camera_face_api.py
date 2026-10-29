"""Privacy-preserving client for the camera's built-in face recognition API."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from xml.etree import ElementTree as ET

import requests


RECORD_TIME_FORMAT = "%Y%m%dT%H%M%S"
SYSTEM_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class CameraApiError(RuntimeError):
    """Base exception that deliberately excludes raw device responses."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class CameraApiAuthenticationError(CameraApiError):
    pass


class CameraApiTransportError(CameraApiError):
    pass


class CameraApiProtocolError(CameraApiError):
    pass


@dataclass(frozen=True)
class CameraOperationResult:
    success: bool
    status_code: int
    message: Optional[str] = None
    image_md5: Optional[str] = None


@dataclass(frozen=True)
class CameraDeviceMetadata:
    """Auditable, read-only device metadata returned by a P6S endpoint."""

    device_id: str
    device_serial: str
    device_model: str
    firmware_version: str
    api_version: str
    endpoint: str
    http_method: str
    retrieved_at: str
    metadata_source: str
    raw_values: Mapping[str, str]
    field_audit: Mapping[str, Mapping[str, str]]


def generate_ownner(unique_code: str, system_time: str) -> str:
    """Generate the vendor's intentionally misspelled ``Ownner`` safety code."""

    if not unique_code:
        raise ValueError("unique_code is required")
    try:
        datetime.strptime(system_time, SYSTEM_TIME_FORMAT)
    except ValueError as exc:
        raise ValueError("system_time must use YYYY-MM-DDThh:mm:ss") from exc
    digest = hashlib.md5(f"{unique_code}{system_time}".encode("utf-8")).hexdigest()
    return digest[:8][::-1]


def _text(element: ET.Element, name: str, default: str = "") -> str:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or default).strip()
    return default


def _int(element: ET.Element, name: str, default: int = 0) -> int:
    value = _text(element, name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(element: ET.Element, name: str) -> bool:
    return _text(element, name).lower() in {"1", "true", "yes"}


def _required_text(
    element: ET.Element, name: str, *, allow_empty: bool = False
) -> str:
    found = next(
        (
            child
            for child in element.iter()
            if child.tag.rsplit("}", 1)[-1] == name
        ),
        None,
    )
    if found is None:
        raise CameraApiProtocolError(
            f"camera recognition record missing required field: {name}",
            retryable=False,
        )
    value = (found.text or "").strip()
    if not value and not allow_empty:
        raise CameraApiProtocolError(
            f"camera recognition record has empty required field: {name}",
            retryable=False,
        )
    return value


def _required_int(element: ET.Element, name: str) -> int:
    value = _required_text(element, name)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CameraApiProtocolError(
            f"camera recognition record has invalid integer field: {name}",
            retryable=False,
        ) from exc


def _required_bool(element: ET.Element, name: str) -> bool:
    value = _required_text(element, name).lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    raise CameraApiProtocolError(
        f"camera recognition record has invalid boolean field: {name}",
        retryable=False,
    )


def _children_named(root: ET.Element, name: str) -> Iterable[ET.Element]:
    if root.tag.rsplit("}", 1)[-1] == name:
        yield root
        return
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            yield element


def _xml(root_name: str, fields: Dict[str, Any]) -> bytes:
    root = ET.Element(root_name)
    for key, value in fields.items():
        child = ET.SubElement(root, key)
        if isinstance(value, bool):
            child.text = "true" if value else "false"
        elif value is not None:
            child.text = str(value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _normalise_metadata_key(value: object) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _metadata_values(payload: object) -> Dict[str, str]:
    values: Dict[str, str] = {}

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(child, (str, int, float, bool)):
                    values.setdefault(_normalise_metadata_key(key), str(child).strip())
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    if isinstance(payload, ET.Element):
        for element in payload.iter():
            key = _normalise_metadata_key(element.tag.rsplit("}", 1)[-1])
            text = (element.text or "").strip()
            if text:
                values.setdefault(key, text)
    else:
        visit(payload)
    return values


def _metadata_field(values: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = values.get(_normalise_metadata_key(name), "")
        if value:
            return value
    return ""


class CameraFaceApiClient:
    """Wrap Basic Auth, XML and safe vendor-to-domain field conversion."""

    def __init__(
        self,
        *,
        device_id: str,
        base_url: str,
        username: str,
        password: str,
        unique_code: str,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        max_retries: int = 2,
        session: Optional[requests.Session] = None,
        metadata_only: bool = False,
    ) -> None:
        if not all((device_id, base_url, username)) or not isinstance(password, str):
            raise ValueError("device_id, base_url and credentials are required")
        if not metadata_only and not unique_code:
            raise ValueError("device_id, base_url, credentials and unique_code are required")
        self.device_id = device_id
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._unique_code = unique_code
        self._timeout = (connect_timeout, read_timeout)
        self._max_retries = max(0, max_retries)
        self._session = session or requests.Session()
        self._metadata_only = metadata_only

    @classmethod
    def from_settings(cls, configured: Optional[Any] = None) -> "CameraFaceApiClient":
        if configured is None:
            from agent.config import settings as configured

        return cls(
            device_id=configured.CAMERA_FACE_DEVICE_ID,
            base_url=configured.CAMERA_FACE_BASE_URL,
            username=configured.CAMERA_FACE_USERNAME,
            password=configured.CAMERA_FACE_PASSWORD,
            unique_code=configured.CAMERA_FACE_UNIQUE_CODE,
            connect_timeout=configured.CAMERA_FACE_CONNECT_TIMEOUT,
            read_timeout=configured.CAMERA_FACE_READ_TIMEOUT,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._metadata_only and method.upper() != "GET":
            raise ValueError("metadata-only camera client permits GET requests only")
        url = f"{self.base_url}{path}"
        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    auth=self._auth,
                    timeout=self._timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if attempt < self._max_retries:
                    time.sleep(min(0.2 * (2**attempt), 1.0))
                    continue
                raise CameraApiTransportError(
                    f"camera {self.device_id} request failed: {type(exc).__name__}",
                    retryable=True,
                ) from exc

            if response.status_code in (401, 403):
                raise CameraApiAuthenticationError(
                    f"camera {self.device_id} authentication failed",
                    retryable=False,
                )
            if 500 <= response.status_code < 600 and attempt < self._max_retries:
                time.sleep(min(0.2 * (2**attempt), 1.0))
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise CameraApiTransportError(
                    f"camera {self.device_id} returned HTTP {response.status_code}",
                    retryable=response.status_code >= 500 or response.status_code == 429,
                )
            return response
        raise AssertionError("unreachable")

    def _request_xml(self, method: str, path: str, **kwargs: Any) -> ET.Element:
        response = self._request(method, path, **kwargs)
        try:
            root = ET.fromstring(response.content)
        except (ET.ParseError, TypeError, ValueError) as exc:
            raise CameraApiProtocolError(
                f"camera {self.device_id} returned malformed XML",
                retryable=False,
            ) from exc
        if root.tag.rsplit("}", 1)[-1] == "ResponseStatus":
            status_code = _int(root, "statusCode", default=-1)
            if status_code != 0:
                raise CameraApiProtocolError(
                    f"camera {self.device_id} operation failed with statusCode={status_code}",
                    retryable=False,
                )
        return root

    def read_device_metadata(
        self,
        *,
        endpoint: str = "/System/DeviceInfo",
    ) -> CameraDeviceMetadata:
        """Read and validate device fingerprint fields using an authenticated GET."""
        if not endpoint.startswith("/") or "?" in endpoint:
            raise ValueError("endpoint must be an absolute path without query parameters")
        response = self._request(
            "GET",
            endpoint,
            headers={"Accept": "application/xml, application/json"},
        )
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        raw_values: Dict[str, str]
        try:
            root = ET.fromstring(response.content)
            raw_values = _metadata_values(root)
        except (ET.ParseError, TypeError, ValueError):
            try:
                raw_values = _metadata_values(json.loads(response.content.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise CameraApiProtocolError(
                    f"camera {self.device_id} returned unsupported device metadata",
                    retryable=False,
                ) from exc

        device_serial = _metadata_field(
            raw_values,
            "serialNumber", "serial", "serialNum", "deviceSerial", "uuid",
            "hardwareId", "mac",
        )
        device_model = _metadata_field(
            raw_values, "deviceModel", "deviceType", "deviceTypeName", "model", "modelName"
        )
        firmware_version = _metadata_field(
            raw_values,
            "firmwareVersion", "firmware", "firmwareVer", "softwareVersion",
            "softWareVersion", "deviceVersion",
        )
        api_version = _metadata_field(
            raw_values, "apiVersion", "apiVer", "protocolVersion"
        )
        if not device_serial or not device_model or not firmware_version:
            raise CameraApiProtocolError(
                f"camera {self.device_id} device metadata missing required fingerprint fields",
                retryable=False,
            )
        normalized = {
            "device_serial": device_serial,
            "device_model": device_model,
            "firmware_version": firmware_version,
            "api_version": api_version,
        }
        field_audit = {
            field_name: {
                "field_name": field_name,
                "raw_value": value,
                "normalized_value": value,
                "endpoint": endpoint,
                "http_method": "GET",
                "retrieved_at": retrieved_at,
                "device_id": self.device_id,
                "source": "authenticated_p6s_metadata",
            }
            for field_name, value in normalized.items()
            if value
        }
        return CameraDeviceMetadata(
            device_id=self.device_id,
            device_serial=device_serial,
            device_model=device_model,
            firmware_version=firmware_version,
            api_version=api_version,
            endpoint=endpoint,
            http_method="GET",
            retrieved_at=retrieved_at,
            metadata_source="authenticated_p6s_metadata",
            raw_values=raw_values,
            field_audit=field_audit,
        )

    def _operation_result(self, root: ET.Element) -> CameraOperationResult:
        status_code = _int(root, "statusCode", default=-1)
        result = CameraOperationResult(
            success=status_code == 0,
            status_code=status_code,
            message=_text(root, "message") or None,
            image_md5=_text(root, "MD5") or None,
        )
        if not result.success:
            raise CameraApiProtocolError(
                f"camera {self.device_id} operation failed with statusCode={status_code}",
                retryable=False,
            )
        return result

    def query_recognition_records(
        self,
        start_time: datetime,
        stop_time: datetime,
        *,
        page_size: int = 100,
        page_num: int = 1,
        group_id: int = -1,
        timezone_offset_minutes: int = 480,
    ) -> List[Dict[str, Any]]:
        if stop_time < start_time:
            raise ValueError("stop_time must be on or after start_time")
        if page_size < 1 or page_num < 1:
            raise ValueError("page_size and page_num must be positive")
        if (
            isinstance(timezone_offset_minutes, bool)
            or not isinstance(timezone_offset_minutes, int)
            or not -1439 <= timezone_offset_minutes <= 1439
        ):
            raise ValueError("timezone_offset_minutes must be a valid integer offset")
        body = _xml(
            "FaceRecognitionFilter",
            {
                "GroupID": group_id,
                "StartTime": start_time.strftime(RECORD_TIME_FORMAT),
                "StopTime": stop_time.strftime(RECORD_TIME_FORMAT),
                "Pagesize": page_size,
                "Pagenum": page_num,
                "IsHasPath": 0,
                "RecognitionResultType": "Comparison pass",
                "PeopleName": "",
                "RuleID": 1,
            },
        )
        root = self._request_xml(
            "PUT",
            "/FaceRecognition/QueryRecordList",
            data=body,
            headers={"Content-Type": "application/xml"},
        )
        records: List[Dict[str, Any]] = []
        for element in _children_named(root, "RecognitionRecord"):
            raw_time = _required_text(element, "Time")
            try:
                occurred_at = datetime.strptime(raw_time, RECORD_TIME_FORMAT).replace(
                    tzinfo=timezone(timedelta(minutes=timezone_offset_minutes))
                ).astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError as exc:
                raise CameraApiProtocolError(
                    f"camera {self.device_id} returned an invalid recognition time",
                    retryable=False,
                ) from exc
            # Keep only fields used by the business flow. In particular, never
            # expose SnapshotPath, health, travel, identity document or test data.
            records.append(
                {
                    "log_id": _required_int(element, "LogID"),
                    "group_id": _required_int(element, "GroupID"),
                    "person_id": _required_int(element, "PersonID"),
                    "occurred_at": occurred_at,
                    "pass_code": _required_int(element, "Pass"),
                    "similarity": _required_int(element, "Similar") / 100.0,
                    "rule_id": _required_int(element, "RuleID"),
                    "reco_valid": _required_bool(element, "RecoValid"),
                    "unique_id": _required_text(
                        element, "UniqueID", allow_empty=True
                    ),
                }
            )
        return records

    def update_person_and_face_image(
        self,
        *,
        alias: str,
        sex: str,
        group_uuid: str,
        face_uuid: str,
        image_bytes: bytes,
        system_time: Optional[datetime] = None,
    ) -> CameraOperationResult:
        if sex not in {"male", "female"}:
            raise ValueError("sex must be explicitly declared as male or female")
        if not alias or len(alias) > 32:
            raise ValueError("alias is required and must be at most 32 characters")
        if not image_bytes:
            raise ValueError("an authorized member photo is required")
        time_text = (system_time or datetime.now()).strftime(SYSTEM_TIME_FORMAT)
        fields = {
            "Name": alias,
            "Sex": sex,
            "Ownner": generate_ownner(self._unique_code, time_text),
            "FaceGroupID": group_uuid,
            "FaceUUID": face_uuid,
            "SystemTime": time_text,
        }
        root = self._request_xml(
            "POST",
            "/FaceGroup/UpdatePersonInfoAndFaceImage",
            data=fields,
            files={"file": ("face.jpg", image_bytes, "image/jpeg")},
        )
        return self._operation_result(root)

    def query_person_info(
        self,
        *,
        group_id: int = -1,
        alias: str = "",
        page_size: int = 100,
        page_num: int = 1,
    ) -> List[Dict[str, Any]]:
        body = _xml(
            "PersonInfoFilter",
            {
                "GroupID": group_id,
                "Name": alias,
                "Sex": "none",
                "CertificateType": "none",
                "CertificateID": "",
                "Country": "",
                "Address": "",
                "StartBirthday": "",
                "StopBirthday": "",
                "Pagesize": page_size,
                "Pagenum": page_num,
            },
        )
        root = self._request_xml(
            "PUT",
            "/FaceGroup/QueryPersonInfoList",
            data=body,
            headers={"Content-Type": "application/xml"},
        )
        return [
            {
                "group_id": _int(item, "GroupID"),
                "person_id": _int(item, "PersonID"),
                "alias": _text(item, "Name"),
                "model_state": _text(item, "ModelState"),
                "face_uuid": _text(item, "FaceUUID"),
                "unique_id": _text(item, "UniqueID"),
            }
            for item in _children_named(root, "PersonInfo")
        ]

    def delete_person(
        self,
        *,
        group_uuid: str,
        face_uuid: str,
        delete_picture: bool = True,
        system_time: Optional[datetime] = None,
    ) -> CameraOperationResult:
        time_text = (system_time or datetime.now()).strftime(SYSTEM_TIME_FORMAT)
        root = ET.Element("PersonInfoList")
        person = ET.SubElement(root, "PersonInfo")
        fields = {
            "IsDeletePicture": delete_picture,
            "Ownner": generate_ownner(self._unique_code, time_text),
            "FaceGroupID": group_uuid,
            "FaceUUID": face_uuid,
            "SystemTime": time_text,
        }
        for key, value in fields.items():
            child = ET.SubElement(person, key)
            child.text = "true" if value is True else "false" if value is False else str(value)
        body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        response_root = self._request_xml(
            "PUT",
            "/FaceGroup/DeletePersonList",
            data=body,
            headers={"Content-Type": "application/xml"},
        )
        return self._operation_result(response_root)

    # ------------------------------------------------------------------
    # Face group management
    # ------------------------------------------------------------------

    def create_face_group(
        self,
        *,
        group_name: str,
        group_uuid: str,
        system_time: Optional[datetime] = None,
        threshold: Optional[float] = None,
    ) -> str:
        """PUT /FaceGroups/Create — create a face group and return its UUID."""
        time_text = (system_time or datetime.now()).strftime(SYSTEM_TIME_FORMAT)
        fields: Dict[str, Any] = {
            "GroupName": group_name,
            "GroupID2": group_uuid,
            "Ownner": generate_ownner(self._unique_code, time_text),
            "SystemTime": time_text,
        }
        if threshold is not None:
            fields["GroupThresholdValue"] = threshold
        body = _xml("FaceGroup", fields)
        root = self._request_xml(
            "PUT",
            "/FaceGroups/Create",
            data=body,
            headers={"Content-Type": "application/xml"},
        )
        returned_uuid = _text(root, "GroupID2")
        return returned_uuid or group_uuid

    def query_face_groups(self) -> List[Dict[str, Any]]:
        """PUT /FaceGroups/QueryAll — list all face groups on the device."""
        root = self._request_xml(
            "PUT",
            "/FaceGroups/QueryAll",
            headers={"Content-Type": "application/xml"},
        )
        groups: List[Dict[str, Any]] = []
        for element in _children_named(root, "FaceGroup"):
            groups.append(
                {
                    "group_id": _int(element, "GroupID"),
                    "group_uuid": _text(element, "GroupID2"),
                    "group_name": _text(element, "GroupName"),
                    "create_time": _text(element, "CreateTime"),
                    "person_count": _int(element, "PersonInfoCount"),
                    "available_status": _text(element, "AvailableStatus"),
                    "valid_person_count": _int(element, "ValidPersonCount"),
                    "invalid_person_count": _int(element, "InvalidPersonCount"),
                    "threshold": (
                        float(_text(element, "GroupThresholdValue"))
                        if _text(element, "GroupThresholdValue")
                        else None
                    ),
                }
            )
        return groups
