"""Restricted, immutable Offline run artifact staging and sealing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Optional, Protocol


class OfflineArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactAccessAssessment:
    status: str
    reason: Optional[str]


class ArtifactStorageSecurityValidator:
    _PUBLIC_COMPONENTS = {"public", "static", "www", "wwwroot", "assets"}

    def validate_filesystem(
        self,
        artifact_root: Path,
        *,
        known_public_roots: Iterable[Path],
        restricted_access_verified: bool,
    ) -> ArtifactAccessAssessment:
        root = Path(artifact_root).resolve()
        public_roots = tuple(Path(item).resolve() for item in known_public_roots)
        if any(_is_within(root, item) for item in public_roots):
            return ArtifactAccessAssessment("violation", "artifact_root_is_public")
        if any(part.lower() in self._PUBLIC_COMPONENTS for part in root.parts):
            return ArtifactAccessAssessment("violation", "artifact_root_is_public")
        if not restricted_access_verified:
            return ArtifactAccessAssessment("unavailable", "restricted_access_unverifiable")
        return ArtifactAccessAssessment("restricted_verified", None)


class OfflineArtifactStorageSecurityPort(Protocol):
    def assess(
        self,
        artifact_root: Path,
        *,
        known_public_roots: Iterable[Path],
    ) -> ArtifactAccessAssessment: ...


class StaticArtifactStorageSecurityAdapter:
    """Explicit test adapter; production must inject a trusted ACL adapter."""

    def __init__(self, status: str = "restricted_verified", reason: Optional[str] = None) -> None:
        self.status = status
        self.reason = reason

    def assess(self, artifact_root: Path, *, known_public_roots: Iterable[Path]) -> ArtifactAccessAssessment:
        return ArtifactStorageSecurityValidator().validate_filesystem(
            artifact_root,
            known_public_roots=known_public_roots,
            restricted_access_verified=self.status == "restricted_verified",
        )


@dataclass(frozen=True)
class ArtifactDefinition:
    relative_path: str
    artifact_type: str
    schema_version: str = "v1"


class OfflineCompleteLoopArtifactPolicyV1:
    policy_id = "offline_complete_loop"
    policy_version = "v1"
    keyframe_policy_version = "v1"
    required_artifacts = (
        ArtifactDefinition("metadata.json", "run_metadata"),
        ArtifactDefinition("source_manifest.json", "source_manifest"),
        ArtifactDefinition("person_tracks.jsonl", "person_tracks"),
        ArtifactDefinition("product_tracks.jsonl", "product_tracks"),
        ArtifactDefinition("identity_evidence.jsonl", "identity_evidence"),
        ArtifactDefinition("track_identity_associations.jsonl", "track_identity_associations"),
        ArtifactDefinition("raw_stage9_events.jsonl", "raw_stage9_events"),
        ArtifactDefinition("normalized_behavior_facts.jsonl", "normalized_behavior_facts"),
        ArtifactDefinition("behavior_summary.json", "behavior_summary"),
        ArtifactDefinition("analyzed_video.mp4", "analyzed_video"),
    )

    @property
    def required_paths(self) -> frozenset[str]:
        return frozenset(item.relative_path for item in self.required_artifacts)

    def definition_for(self, relative_path: str) -> ArtifactDefinition:
        for item in self.required_artifacts:
            if item.relative_path == relative_path:
                return item
        if relative_path.startswith("keyframes/"):
            return ArtifactDefinition(relative_path, "behavior_keyframe")
        return ArtifactDefinition(relative_path, "run_diagnostic")


@dataclass(frozen=True)
class OfflineArtifactManifestEntry:
    relative_path: str
    artifact_type: str
    schema_version: str
    size_bytes: int
    sha256: str
    required_for_publication: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "required_for_publication": self.required_for_publication,
        }


class OfflineArtifactManifestCanonicalizerV1:
    def canonical_bytes(self, body: dict[str, Any]) -> bytes:
        normalized = dict(body)
        normalized["artifacts"] = sorted(
            body.get("artifacts", ()), key=lambda item: item["relative_path"]
        )
        return json.dumps(
            normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


@dataclass(frozen=True)
class SealedOfflineRunArtifacts:
    analysis_job_id: str
    analysis_run_id: str
    source_video_id: str
    source_content_sha256: str
    run_directory: Path
    artifact_manifest_hash: str
    run_seal_status: str
    artifact_access_status: str


@dataclass(frozen=True)
class OfflineArtifactVerification:
    artifact_integrity_status: str
    keyframe_validation_status: str
    run_seal_status: str
    artifact_access_status: str
    reason: Optional[str]

    @property
    def publication_eligible(self) -> bool:
        return all((
            self.artifact_integrity_status == "verified",
            self.keyframe_validation_status == "satisfied",
            self.run_seal_status == "sealed",
            self.artifact_access_status == "restricted_verified",
        ))


class OfflineRunArtifactWriterPort(Protocol):
    """Application-facing writer contract for one run's restricted artifacts."""

    staging_directory: Path

    def write_json(self, relative_path: str, value: Any) -> None: ...

    def write_jsonl(self, relative_path: str, values: Iterable[Any]) -> None: ...

    def write_bytes(self, relative_path: str, value: bytes) -> None: ...

    def seal(
        self,
        *,
        source_video_id: str,
        source_content_sha256: str,
        normalized_facts: Iterable[Any],
    ) -> SealedOfflineRunArtifacts: ...


class OfflineRunArtifactStorePort(Protocol):
    """Minimal artifact storage capability consumed by Offline application code."""

    def begin(
        self, *, analysis_job_id: str, analysis_run_id: str
    ) -> OfflineRunArtifactWriterPort: ...

    def verify(
        self, sealed: SealedOfflineRunArtifacts
    ) -> OfflineArtifactVerification: ...


class OfflineRunArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        security_adapter: OfflineArtifactStorageSecurityPort,
        known_public_roots: Iterable[Path],
        media_probe: Optional[Callable[[Path], bool]] = None,
        policy: Optional[OfflineCompleteLoopArtifactPolicyV1] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.security_adapter = security_adapter
        self.known_public_roots = tuple(known_public_roots)
        self.media_probe = media_probe
        self.policy = policy or OfflineCompleteLoopArtifactPolicyV1()

    def begin(self, *, analysis_job_id: str, analysis_run_id: str) -> "OfflineRunArtifactWriter":
        _scope_component(analysis_job_id)
        _scope_component(analysis_run_id)
        access = self.security_adapter.assess(
            self.root,
            known_public_roots=self.known_public_roots,
        )
        if access.status != "restricted_verified":
            raise OfflineArtifactError(access.reason or "artifact_access_unavailable")
        staging = self.root / ".staging" / analysis_job_id / analysis_run_id
        final = self.root / analysis_job_id / analysis_run_id
        if staging.exists() or final.exists():
            raise OfflineArtifactError("run_artifact_collision")
        staging.mkdir(parents=True, exist_ok=False)
        return OfflineRunArtifactWriter(
            store=self,
            analysis_job_id=analysis_job_id,
            analysis_run_id=analysis_run_id,
            staging_directory=staging,
            final_directory=final,
        )

    def verify(self, sealed: SealedOfflineRunArtifacts) -> OfflineArtifactVerification:
        access = self.security_adapter.assess(
            self.root,
            known_public_roots=self.known_public_roots,
        )
        if access.status != "restricted_verified":
            return OfflineArtifactVerification(
                "unavailable", "unavailable", "sealed", access.status, access.reason
            )
        expected_directory = (
            self.root / sealed.analysis_job_id / sealed.analysis_run_id
        ).resolve()
        try:
            actual_directory = sealed.run_directory.resolve()
        except OSError:
            return OfflineArtifactVerification(
                "unavailable", "unavailable", "unavailable", access.status,
                "run_directory_unavailable",
            )
        if actual_directory != expected_directory or not _is_within(
            actual_directory, self.root
        ):
            return OfflineArtifactVerification(
                "conflict", "unavailable", "unavailable", access.status,
                "run_directory_scope_mismatch",
            )
        if sealed.run_seal_status != "sealed":
            return OfflineArtifactVerification(
                "unavailable", "unavailable", sealed.run_seal_status, access.status,
                "run_not_sealed",
            )
        if sealed.artifact_access_status != "restricted_verified":
            return OfflineArtifactVerification(
                "unavailable", "unavailable", sealed.run_seal_status, access.status,
                "sealed_artifact_access_unverified",
            )
        manifest_path = sealed.run_directory / "artifact_manifest.json"
        if not manifest_path.is_file():
            return OfflineArtifactVerification(
                "unavailable", "unavailable", "unavailable", access.status,
                "artifact_manifest_missing",
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            body = manifest["body"]
            expected_hash = manifest["artifact_manifest_hash"]
            actual_hash = "v1:sha256:" + hashlib.sha256(
                OfflineArtifactManifestCanonicalizerV1().canonical_bytes(body)
            ).hexdigest()
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return OfflineArtifactVerification(
                "conflict", "unavailable", "sealed", access.status,
                "artifact_manifest_invalid",
            )
        if actual_hash != expected_hash or expected_hash != sealed.artifact_manifest_hash:
            return OfflineArtifactVerification(
                "conflict", "unavailable", "sealed", access.status,
                "artifact_manifest_hash_mismatch",
            )
        if any((
            body.get("analysis_job_id") != sealed.analysis_job_id,
            body.get("analysis_run_id") != sealed.analysis_run_id,
            body.get("source_video_id") != sealed.source_video_id,
            body.get("source_content_sha256") != sealed.source_content_sha256,
        )):
            return OfflineArtifactVerification(
                "conflict", "unavailable", sealed.run_seal_status, access.status,
                "artifact_manifest_scope_mismatch",
            )
        entries = body.get("artifacts", ())
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            return OfflineArtifactVerification(
                "conflict", "unavailable", "sealed", access.status,
                "artifact_manifest_entries_invalid",
            )
        if entries != sorted(
            entries, key=lambda item: item.get("relative_path", "")
        ):
            return OfflineArtifactVerification(
                "conflict", "unavailable", "sealed", access.status,
                "artifact_manifest_order_invalid",
            )
        try:
            paths = {entry["relative_path"] for entry in entries}
        except (KeyError, TypeError):
            return OfflineArtifactVerification(
                "conflict", "unavailable", "sealed", access.status,
                "artifact_manifest_entry_invalid",
            )
        if not self.policy.required_paths <= paths:
            return OfflineArtifactVerification(
                "unavailable", "unavailable", "sealed", access.status,
                "required_artifact_missing",
            )
        required_entries = {
            entry.get("relative_path")
            for entry in entries
            if entry.get("required_for_publication") is True
        }
        if not self.policy.required_paths <= required_entries:
            return OfflineArtifactVerification(
                "conflict", "unavailable", "sealed", access.status,
                "required_artifact_policy_mismatch",
            )
        for entry in entries:
            try:
                path = sealed.run_directory / Path(entry["relative_path"])
                size_bytes = entry["size_bytes"]
                digest = entry["sha256"]
            except (KeyError, TypeError, ValueError):
                return OfflineArtifactVerification(
                    "conflict", "unavailable", "sealed", access.status,
                    "artifact_manifest_entry_invalid",
                )
            if not path.is_file():
                return OfflineArtifactVerification(
                    "unavailable", "unavailable", "sealed", access.status,
                    "artifact_missing",
                )
            if path.stat().st_size != size_bytes or _sha256_file(path) != digest:
                return OfflineArtifactVerification(
                    "conflict", "unavailable", "sealed", access.status,
                    "artifact_hash_mismatch",
                )
        actual_paths = {
            path.relative_to(sealed.run_directory).as_posix()
            for path in sealed.run_directory.rglob("*")
            if path.is_file() and path.name != "artifact_manifest.json"
        }
        if actual_paths != paths:
            return OfflineArtifactVerification(
                "conflict", "unavailable", "sealed", access.status,
                "artifact_manifest_directory_mismatch",
            )
        keyframe_status = body.get("keyframe_validation_status")
        if not _namespace_is_read_only(sealed.run_directory):
            return OfflineArtifactVerification(
                "unavailable", "unavailable", sealed.run_seal_status,
                access.status, "artifact_namespace_mutable",
            )
        return OfflineArtifactVerification(
            "verified",
            keyframe_status if keyframe_status == "satisfied" else "unavailable",
            sealed.run_seal_status,
            access.status,
            None if keyframe_status == "satisfied" else "keyframe_validation_failed",
        )


class OfflineRunArtifactWriter:
    def __init__(
        self,
        *,
        store: OfflineRunArtifactStore,
        analysis_job_id: str,
        analysis_run_id: str,
        staging_directory: Path,
        final_directory: Path,
    ) -> None:
        self.store = store
        self.analysis_job_id = analysis_job_id
        self.analysis_run_id = analysis_run_id
        self.staging_directory = staging_directory
        self.final_directory = final_directory
        self._written: set[str] = set()
        self._sealed = False

    def write_json(self, relative_path: str, value: Any) -> None:
        payload = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        self.write_bytes(relative_path, payload)

    def write_jsonl(self, relative_path: str, values: Iterable[Any]) -> None:
        lines = [
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            for value in values
        ]
        payload = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
        self.write_bytes(relative_path, payload)

    def write_bytes(self, relative_path: str, value: bytes) -> None:
        if self._sealed:
            raise OfflineArtifactError("run_already_sealed")
        relative = _relative_path(relative_path)
        if relative in self._written or relative == "artifact_manifest.json":
            raise OfflineArtifactError("artifact_path_collision")
        if not isinstance(value, bytes):
            raise ValueError("artifact_bytes_required")
        destination = self.staging_directory / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        self._written.add(relative)

    def seal(
        self,
        *,
        source_video_id: str,
        source_content_sha256: str,
        normalized_facts: Iterable[Any],
    ) -> SealedOfflineRunArtifacts:
        if self._sealed:
            raise OfflineArtifactError("run_already_sealed")
        if not self.store.policy.required_paths <= self._written:
            raise OfflineArtifactError("required_artifact_missing")
        if self.store.media_probe is None:
            raise OfflineArtifactError("analyzed_video_probe_unavailable")
        video_path = self.staging_directory / "analyzed_video.mp4"
        try:
            media_valid = self.store.media_probe(video_path)
        except Exception as exc:
            raise OfflineArtifactError("analyzed_video_probe_failed") from exc
        if not media_valid:
            raise OfflineArtifactError("analyzed_video_invalid")
        if len(source_content_sha256) != 64:
            raise OfflineArtifactError("source_content_hash_invalid")
        self._verify_source_manifest(source_video_id, source_content_sha256)
        _validate_required_artifact_schemas(
            self.staging_directory, self.store.policy
        )

        facts = tuple(normalized_facts)
        required_keyframes = _required_keyframes(facts)
        for relative in required_keyframes:
            if relative not in self._written:
                raise OfflineArtifactError("required_keyframe_missing")

        entries = []
        for relative in sorted(self._written):
            path = self.staging_directory / Path(relative)
            definition = self.store.policy.definition_for(relative)
            entries.append(OfflineArtifactManifestEntry(
                relative_path=relative,
                artifact_type=definition.artifact_type,
                schema_version=definition.schema_version,
                size_bytes=path.stat().st_size,
                sha256=_sha256_file(path),
                required_for_publication=(
                    relative in self.store.policy.required_paths
                    or relative in required_keyframes
                ),
            ))
        body = {
            "manifest_version": "offline_run_artifact_manifest_v1",
            "artifact_schema_version": "v1",
            "analysis_job_id": self.analysis_job_id,
            "analysis_run_id": self.analysis_run_id,
            "source_video_id": source_video_id,
            "source_content_sha256": source_content_sha256,
            "data_classification": "RESTRICTED_AUDIT",
            "publication_artifact_policy_id": self.store.policy.policy_id,
            "publication_artifact_policy_version": self.store.policy.policy_version,
            "required_artifact_validation_status": "verified",
            "keyframe_policy_version": self.store.policy.keyframe_policy_version,
            "required_pick_up_keyframes": sum(
                1 for item in facts if _fact_type(item) == "merchandise_pick_up"
            ),
            "required_put_down_keyframes": sum(
                1 for item in facts if _fact_type(item) == "merchandise_put_down"
            ),
            "resolved_keyframe_count": len(required_keyframes),
            "keyframe_validation_status": "satisfied",
            "artifacts": [entry.to_dict() for entry in entries],
        }
        canonical = OfflineArtifactManifestCanonicalizerV1().canonical_bytes(body)
        manifest_hash = "v1:sha256:" + hashlib.sha256(canonical).hexdigest()
        manifest_path = self.staging_directory / "artifact_manifest.json"
        with manifest_path.open("xb") as output:
            output.write(json.dumps(
                {"artifact_manifest_hash": manifest_hash, "body": body},
                ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8") + b"\n")
            output.flush()
            os.fsync(output.fileno())

        self.final_directory.parent.mkdir(parents=True, exist_ok=True)
        if self.final_directory.exists():
            raise OfflineArtifactError("run_artifact_collision")
        _make_namespace_read_only(self.staging_directory)
        _replace_directory_with_transient_retry(
            self.staging_directory, self.final_directory
        )
        self._sealed = True
        sealed = SealedOfflineRunArtifacts(
            analysis_job_id=self.analysis_job_id,
            analysis_run_id=self.analysis_run_id,
            source_video_id=source_video_id,
            source_content_sha256=source_content_sha256,
            run_directory=self.final_directory,
            artifact_manifest_hash=manifest_hash,
            run_seal_status="sealed",
            artifact_access_status="restricted_verified",
        )
        verification = self.store.verify(sealed)
        if not verification.publication_eligible:
            raise OfflineArtifactError(verification.reason or "artifact_verification_failed")
        return sealed

    def _verify_source_manifest(self, source_video_id: str, content_hash: str) -> None:
        try:
            manifest = json.loads(
                (self.staging_directory / "source_manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise OfflineArtifactError("source_manifest_invalid") from exc
        if manifest.get("source_video_id") != source_video_id:
            raise OfflineArtifactError("source_video_id_mismatch")
        declared_hash = manifest.get(
            "download_content_sha256", manifest.get("source_content_sha256")
        )
        if declared_hash != content_hash:
            raise OfflineArtifactError("source_content_hash_mismatch")


def _required_keyframes(facts: tuple[Any, ...]) -> frozenset[str]:
    by_session: dict[str, set[str]] = {}
    refs: set[str] = set()
    for fact in facts:
        event_type = _fact_type(fact)
        session = _fact_field(fact, "interaction_session_id")
        if event_type in {"merchandise_pick_up", "merchandise_put_down"}:
            reference = _fact_field(fact, "keyframe_ref")
            if not isinstance(reference, str) or not reference:
                raise OfflineArtifactError("required_keyframe_ref_missing")
            reference = _relative_path(reference)
            if not reference.startswith("keyframes/"):
                raise OfflineArtifactError("keyframe_ref_outside_run")
            refs.add(reference)
        by_session.setdefault(str(session), set()).add(event_type)
    for types in by_session.values():
        if "merchandise_holding" in types and not {
            "merchandise_pick_up", "merchandise_put_down"
        } <= types:
            raise OfflineArtifactError("holding_boundary_fact_missing")
    return frozenset(refs)


def _validate_required_artifact_schemas(
    directory: Path, policy: OfflineCompleteLoopArtifactPolicyV1
) -> None:
    """Validate the V1 record shapes before hashes and the seal are produced."""
    try:
        metadata = _read_json_object(directory / "metadata.json")
        source = _read_json_object(directory / "source_manifest.json")
        summary = _read_json_object(directory / "behavior_summary.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise OfflineArtifactError("artifact_schema_invalid") from exc
    required_metadata = {
        "analysis_job_id", "analysis_run_id", "store_id", "camera_id",
        "event_time_standard", "media_timeline_status", "behavior_time_quality",
    }
    if not required_metadata <= metadata.keys() or metadata.get("event_time_standard") != "utc_naive":
        raise OfflineArtifactError("metadata_schema_invalid")
    if not {
        "source_video_id", "source_content_sha256", "source_video_integrity_status"
    } <= source.keys():
        raise OfflineArtifactError("source_manifest_schema_invalid")
    if not {
        "summary_version", "event_count", "events_by_type",
        "events_by_confirmed_member_id", "publication_eligible",
    } <= summary.keys():
        raise OfflineArtifactError("behavior_summary_schema_invalid")
    if not isinstance(summary["event_count"], int) or summary["event_count"] < 0:
        raise OfflineArtifactError("behavior_summary_schema_invalid")
    if not isinstance(summary["events_by_type"], dict) or not isinstance(
        summary["events_by_confirmed_member_id"], dict
    ):
        raise OfflineArtifactError("behavior_summary_schema_invalid")
    for definition in policy.required_artifacts:
        path = directory / definition.relative_path
        if definition.relative_path.endswith(".jsonl"):
            _validate_jsonl_records(path, definition.artifact_type)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _validate_jsonl_records(path: Path, artifact_type: str) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise OfflineArtifactError(f"{artifact_type}_schema_invalid")
        if artifact_type == "track_identity_associations":
            required = {"analysis_run_id", "track_token", "status", "valid_from", "valid_to"}
            if not required <= value.keys():
                raise OfflineArtifactError("track_identity_associations_schema_invalid")
        elif artifact_type == "normalized_behavior_facts":
            required = {
                "analysis_job_id", "analysis_run_id", "event_type", "camera_id",
                "customer_track_token", "product_track_token", "started_at", "ended_at",
            }
            if not required <= value.keys():
                raise OfflineArtifactError("normalized_behavior_facts_schema_invalid")
        elif artifact_type == "raw_stage9_events":
            required = {"event_id", "event_type", "camera_id"}
            if not required <= value.keys():
                raise OfflineArtifactError("raw_stage9_events_schema_invalid")


def _make_namespace_read_only(directory: Path) -> None:
    try:
        for path in directory.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError as exc:
        raise OfflineArtifactError("artifact_namespace_immutable_unavailable") from exc


def _replace_directory_with_transient_retry(source: Path, destination: Path) -> None:
    """Retry only transient Windows-style access errors at the atomic rename."""

    for attempt in range(4):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (2 ** attempt))


def _namespace_is_read_only(directory: Path) -> bool:
    try:
        return all(
            not (path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
            for path in directory.rglob("*")
            if path.is_file()
        )
    except OSError:
        return False


def _fact_type(value: Any) -> str:
    return str(_fact_field(value, "event_type"))


def _fact_field(value: Any, field: str) -> Any:
    return value.get(field) if isinstance(value, dict) else getattr(value, field, None)


def _relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("artifact_relative_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact_relative_path_invalid")
    return path.as_posix()


def _scope_component(value: str) -> None:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError("artifact_scope_invalid")
    if any(character in value for character in ("/", "\\", ":")):
        raise ValueError("artifact_scope_invalid")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
