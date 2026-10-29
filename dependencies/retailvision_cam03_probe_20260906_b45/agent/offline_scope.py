"""Offline V1 run-scope validation for behavior and identity artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class OfflineRunScopeValidation:
    status: str
    reason: Optional[str]

    @property
    def valid(self) -> bool:
        return self.status == "valid"


class OfflineRunScopeValidator:
    """Fail closed when an Offline fact crosses its job/run camera scope."""

    def validate_behavior(
        self,
        *,
        store_id: str,
        camera_id: str,
        analysis_run_id: str,
        raw_stage9_events: Iterable[Any],
        person_tracks: Iterable[Any],
        product_tracks: Iterable[Any],
    ) -> OfflineRunScopeValidation:
        for event in raw_stage9_events:
            if _field(event, "camera_id") != camera_id:
                return _conflict("normalization_contract_conflict")
            if not _person_token_matches(
                _field(event, "person_track_token"),
                store_id=store_id,
                camera_id=camera_id,
                analysis_run_id=analysis_run_id,
                track_id=_field(event, "person_track_id"),
            ):
                return _conflict("normalization_contract_conflict")
            if not _product_token_matches(
                _field(event, "product_track_token"),
                camera_id=camera_id,
                analysis_run_id=analysis_run_id,
            ):
                return _conflict("normalization_contract_conflict")

        for track in person_tracks:
            token = _field(track, "track_token")
            if not _person_token_matches(
                token,
                store_id=store_id,
                camera_id=camera_id,
                analysis_run_id=analysis_run_id,
                track_id=_optional_track_id(track),
            ):
                return _conflict("normalization_contract_conflict")

        for track in product_tracks:
            token = _field(track, "track_token") or _field(track, "product_track_token")
            if not _product_token_matches(
                token,
                camera_id=camera_id,
                analysis_run_id=analysis_run_id,
            ):
                return _conflict("normalization_contract_conflict")
        return OfflineRunScopeValidation("valid", None)

    def validate_facts(
        self,
        *,
        store_id: str,
        camera_id: str,
        analysis_run_id: str,
        facts: Iterable[Any],
    ) -> OfflineRunScopeValidation:
        for fact in facts:
            if (
                _field(fact, "camera_id") != camera_id
                or _field(fact, "analysis_run_id") != analysis_run_id
                or not _person_token_matches(
                    _field(fact, "customer_track_token"),
                    store_id=store_id,
                    camera_id=camera_id,
                    analysis_run_id=analysis_run_id,
                    track_id=_field(fact, "customer_track_id"),
                )
                or not _product_token_matches(
                    _field(fact, "product_track_token"),
                    camera_id=camera_id,
                    analysis_run_id=analysis_run_id,
                )
            ):
                return _conflict("normalization_contract_conflict")
        return OfflineRunScopeValidation("valid", None)

    def validate_associations(
        self,
        *,
        store_id: str,
        camera_id: str,
        analysis_run_id: str,
        associations: Mapping[str, Any],
    ) -> OfflineRunScopeValidation:
        for key, association in associations.items():
            token = _field(association, "track_token")
            if (
                key != token
                or _field(association, "analysis_run_id") != analysis_run_id
                or not _person_token_matches(
                    token,
                    store_id=store_id,
                    camera_id=camera_id,
                    analysis_run_id=analysis_run_id,
                    track_id=_field(association, "global_track_id"),
                )
            ):
                return _conflict("identity_contract_conflict")
            association_store = _field(association, "store_id")
            association_camera = _field(association, "camera_id")
            if association_store is not None and association_store != store_id:
                return _conflict("identity_contract_conflict")
            if association_camera is not None and association_camera != camera_id:
                return _conflict("identity_contract_conflict")
        return OfflineRunScopeValidation("valid", None)


def _person_token_matches(
    token: Any,
    *,
    store_id: str,
    camera_id: str,
    analysis_run_id: str,
    track_id: Any,
) -> bool:
    if not isinstance(token, str):
        return False
    parts = token.split(":")
    if len(parts) != 4 or parts[:3] != [store_id, camera_id, analysis_run_id]:
        return False
    if not parts[3] or ":" in analysis_run_id:
        return False
    if track_id is None:
        return True
    return not isinstance(track_id, bool) and str(track_id) == parts[3]


def _product_token_matches(
    token: Any,
    *,
    camera_id: str,
    analysis_run_id: str,
) -> bool:
    if not isinstance(token, str):
        return False
    parts = token.split(":")
    return (
        len(parts) == 4
        and parts[0] == "product"
        and parts[1] == camera_id
        and parts[2] == analysis_run_id
        and bool(parts[3])
    )


def _optional_track_id(value: Any) -> Any:
    for name in ("global_track_id", "track_id", "person_track_id"):
        item = _field(value, name)
        if item is not None:
            return item
    return None


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _conflict(reason: str) -> OfflineRunScopeValidation:
    return OfflineRunScopeValidation("conflict", reason)
