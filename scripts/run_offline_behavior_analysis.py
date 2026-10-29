"""Run a configured Offline V1 analysis adapter under the frozen gates."""

from __future__ import annotations

import argparse
import importlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.offline_artifacts import OfflineRunArtifactStore
from agent.offline_behavior import OfflineBehaviorIdentityProjector, Stage9BehaviorFactAdapter
from agent.config import Settings
from agent.product_interaction_analytics import ProductInteractionFactWriter
from agent.offline_publication import OfflineBehaviorPublisher
from agent.offline_publication import OfflineBehaviorTimePublicationGate
from agent.offline_publication_sql import SqlAlchemyOfflinePublicationRepository
from agent.behavior_events import BehaviorEventStore
from agent.offline_media import CapabilityProfileResolution, SegmentDurationAssessment
from agent.offline_source import P6SRecordingSource
from agent.offline_source import TrustedSourceVideoResult
from agent.offline_source import RegistrationScopeValidator
from agent.offline_video_models import OfflineVideoAnalysisRequest
from pipelines.offline_behavior_pipeline import OfflineBehaviorPipeline
from agent.schema_evolution import evolve_schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--behavior-analyzer", required=True, help="module:callable")
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--media-probe", default=None, help="module:callable returning bool")
    parser.add_argument(
        "--storage-security-adapter", required=True,
        help="module:callable returning the trusted artifact storage security adapter",
    )
    parser.add_argument("--public-root", action="append", type=Path, default=[])
    args = parser.parse_args()

    request = _request_from_plan(json.loads(args.plan.read_text(encoding="utf-8")))
    analyzer = _load_callable(args.behavior_analyzer)
    store = OfflineRunArtifactStore(
        args.artifact_root,
        security_adapter=_load_callable(args.storage_security_adapter)(),
        known_public_roots=args.public_root,
        media_probe=None if args.media_probe is None else _load_callable(args.media_probe),
    )
    publisher = None
    product_analytics_writer = None
    if Settings.PRODUCT_ANALYTICS_PROJECTION_ENABLED and not args.db_url:
        raise ValueError("product_analytics_projection_database_required")
    track_hash_secret = Settings.PRODUCT_ANALYTICS_TRACK_HASH_SECRET.strip()
    if Settings.PRODUCT_ANALYTICS_PROJECTION_ENABLED and not track_hash_secret:
        raise ValueError("product_analytics_track_hash_secret_required")
    if args.db_url:
        engine = create_engine(args.db_url)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        publisher = OfflineBehaviorPublisher(
            publication_repository=SqlAlchemyOfflinePublicationRepository(
                sessions, BehaviorEventStore(sessions)
            ),
        )
        if Settings.PRODUCT_ANALYTICS_PROJECTION_ENABLED:
            product_analytics_writer = ProductInteractionFactWriter(
                sessions, track_hash_secret=track_hash_secret.encode("utf-8")
            )
    pipeline = OfflineBehaviorPipeline(
        artifact_store=store,
        behavior_analyzer=analyzer,
        trusted_source_provider=_cli_untrusted_source,
        recording_time_provider=lambda _request: None,
        provider_a_profile=CapabilityProfileResolution(
            status="unavailable", profile=None,
            reason="cli_has_no_real_device_capability",
        ),
        segment_duration_assessor=lambda _request, _behavior: SegmentDurationAssessment(
            "unavailable", None, None, None, None,
            "cli_has_no_trusted_segment_duration_assessor",
        ),
        fact_normalizer=Stage9BehaviorFactAdapter(),
        identity_projector=OfflineBehaviorIdentityProjector(),
        time_publication_gate=OfflineBehaviorTimePublicationGate(),
        registration_validator=RegistrationScopeValidator(),
        publisher=publisher,
        product_analytics_writer=product_analytics_writer,
        product_analytics_enabled=Settings.PRODUCT_ANALYTICS_PROJECTION_ENABLED,
    )
    result = pipeline.run(request=request)
    print(json.dumps({
        "source_video_integrity_status": result.source_video_integrity_status,
        "registration_scope_status": result.registration_scope_status,
        "segment_duration_status": result.segment_duration_status,
        "media_timeline_status": result.media_timeline_status,
        "absolute_event_time_status": result.absolute_event_time_status,
        "identity_status": result.identity_status,
        "behavior_time_quality": result.behavior_time_quality,
        "publication_eligible": result.publication_eligible,
        "publication_block_reason": result.publication_block_reason,
        "artifact_directory": result.artifact_directory,
        "artifact_manifest_hash": result.artifact_manifest_hash,
        "publication_status": result.publication_status,
        "product_analytics_projection_status": result.product_analytics_projection_status,
        "product_analytics_projection_reason": result.product_analytics_projection_reason,
    }, ensure_ascii=True, sort_keys=True))
    return 0


def _request_from_plan(plan: dict) -> OfflineVideoAnalysisRequest:
    recording_data = plan.get("recording")
    recording = None
    if recording_data is not None:
        recording = P6SRecordingSource(
            device_id=recording_data["device_id"],
            path_name=recording_data["path_name"],
            file_name=recording_data["file_name"],
            raw_started_at=_naive_datetime(recording_data["raw_started_at"]),
            raw_ended_at=_naive_datetime(recording_data["raw_ended_at"]),
            indexed_size_bytes=recording_data["indexed_size_bytes"],
        )
    return OfflineVideoAnalysisRequest(
        analysis_job_id=plan["analysis_job_id"],
        analysis_run_id=plan["analysis_run_id"],
        video_path=Path(plan["video_path"]),
        store_id=plan["store_id"],
        camera_id=plan["camera_id"],
        device_id=plan.get("device_id"),
        source_origin=plan.get("source_origin", "untrusted_import"),
        recording=recording,
        controlled_download=bool(plan.get("controlled_download", False)),
        timezone_offset_minutes=plan.get("timezone_offset_minutes"),
        timezone_source=plan.get("timezone_source", ""),
        timezone_verified=bool(plan.get("timezone_verified", False)),
        analysis_fps=plan.get("analysis_fps", 5.0),
        # Caller-provided status is retained only as an untrusted diagnostic.
        segment_duration_status=plan.get("segment_duration_status", "unavailable"),
        # This CLI does not configure real-device Provider A qualification.
        provider_a_status="unavailable",
    )


def _load_callable(value: str):
    module_name, separator, attribute = value.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("behavior_analyzer must use module:callable")
    return getattr(importlib.import_module(module_name), attribute)


def _cli_untrusted_source(request: OfflineVideoAnalysisRequest) -> TrustedSourceVideoResult:
    return TrustedSourceVideoResult(
        status="untrusted", source_video_id=None, content_sha256=None,
        indexed_size_bytes=(request.recording.indexed_size_bytes if request.recording else 0),
        downloaded_size_bytes=None, reason="cli_has_no_controlled_p6s_source_adapter",
        canonical_manifest=None,
    )


def _naive_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("recording plan time must be device-local naive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
