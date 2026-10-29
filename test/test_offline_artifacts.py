from __future__ import annotations

import tempfile
import unittest
import stat
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.offline_artifacts import (  # noqa: E402
    ArtifactStorageSecurityValidator,
    OfflineArtifactError,
    OfflineRunArtifactStore,
    StaticArtifactStorageSecurityAdapter,
)


class OfflineArtifactContractTests(unittest.TestCase):
    def test_complete_empty_run_seals_and_verifies_deterministically(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer)

            sealed = writer.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )
            verification = store.verify(sealed)

            self.assertEqual("sealed", sealed.run_seal_status)
            self.assertTrue(sealed.artifact_manifest_hash.startswith("v1:sha256:"))
            self.assertEqual("verified", verification.artifact_integrity_status)
            self.assertEqual("satisfied", verification.keyframe_validation_status)
            self.assertEqual("restricted_verified", verification.artifact_access_status)

    def test_missing_required_artifact_cannot_expose_final_run(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer, omit="analyzed_video.mp4")

            with self.assertRaisesRegex(OfflineArtifactError, "required_artifact_missing"):
                writer.seal(
                    source_video_id="video", source_content_sha256="a" * 64,
                    normalized_facts=(),
                )

            self.assertFalse((Path(root) / "job" / "run").exists())

    def test_modified_sealed_artifact_is_reported_as_integrity_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer)
            sealed = writer.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )
            summary = sealed.run_directory / "behavior_summary.json"
            summary.chmod(stat.S_IWRITE | stat.S_IREAD)
            summary.write_text("changed", encoding="utf-8")

            result = store.verify(sealed)

            self.assertEqual("conflict", result.artifact_integrity_status)
            self.assertEqual("artifact_hash_mismatch", result.reason)

    def test_extra_file_after_seal_is_an_integrity_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer)
            sealed = writer.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )
            (sealed.run_directory / "late-debug.json").write_text("{}", encoding="utf-8")

            result = store.verify(sealed)

            self.assertEqual(
                ("conflict", "artifact_manifest_directory_mismatch"),
                (result.artifact_integrity_status, result.reason),
            )

    def test_forged_sealed_scope_cannot_point_publisher_at_another_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            store = self._store(root_path)
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer)
            sealed = writer.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )

            outside = replace(sealed, run_directory=root_path / "other" / "run")
            source_mismatch = replace(sealed, source_video_id="other-video")

            self.assertEqual(
                "run_directory_scope_mismatch", store.verify(outside).reason
            )
            self.assertEqual(
                "artifact_manifest_scope_mismatch", store.verify(source_mismatch).reason
            )

    def test_public_or_static_artifact_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            public = Path(root) / "public"
            public.mkdir()
            assessment = ArtifactStorageSecurityValidator().validate_filesystem(
                public / "analysis_results",
                known_public_roots=(public,),
                restricted_access_verified=True,
            )

            self.assertEqual(("violation", "artifact_root_is_public"), (assessment.status, assessment.reason))

    def test_required_artifact_schema_is_validated_before_seal(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer, omit="behavior_summary.json")
            writer.write_json("behavior_summary.json", {"summary_version": "v1"})

            with self.assertRaisesRegex(OfflineArtifactError, "behavior_summary_schema_invalid"):
                writer.seal(
                    source_video_id="video", source_content_sha256="a" * 64,
                    normalized_facts=(),
                )

    def test_sealed_files_are_read_only_and_namespace_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer)
            sealed = writer.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )

            self.assertFalse(
                (sealed.run_directory / "metadata.json").stat().st_mode & stat.S_IWUSR
            )
            with self.assertRaisesRegex(OfflineArtifactError, "run_artifact_collision"):
                store.begin(analysis_job_id="job", analysis_run_id="run")

    def test_transient_permission_error_at_atomic_finalize_is_retried(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer)
            from agent import offline_artifacts

            real_replace = offline_artifacts.os.replace
            calls = 0

            def transient_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("transient scanner handle")
                return real_replace(source, destination)

            with patch("agent.offline_artifacts.os.replace", side_effect=transient_replace):
                sealed = writer.seal(
                    source_video_id="video",
                    source_content_sha256="a" * 64,
                    normalized_facts=(),
                )

            self.assertEqual(2, calls)
            self.assertEqual("verified", store.verify(sealed).artifact_integrity_status)

    @staticmethod
    def _store(root: Path):
        return OfflineRunArtifactStore(
            root,
            security_adapter=StaticArtifactStorageSecurityAdapter(),
            known_public_roots=(),
            media_probe=lambda path: path.stat().st_size > 0,
        )

    @staticmethod
    def _write_required(writer, omit=None):
        json_files = ("metadata.json", "source_manifest.json", "behavior_summary.json")
        jsonl_files = (
            "person_tracks.jsonl", "product_tracks.jsonl", "identity_evidence.jsonl",
            "track_identity_associations.jsonl", "raw_stage9_events.jsonl",
            "normalized_behavior_facts.jsonl",
        )
        for name in json_files:
            if name != omit:
                if name == "metadata.json":
                    payload = {
                        "analysis_job_id": writer.analysis_job_id,
                        "analysis_run_id": writer.analysis_run_id,
                        "store_id": "store",
                        "camera_id": "cam",
                        "event_time_standard": "utc_naive",
                        "media_timeline_status": "available",
                        "behavior_time_quality": "authoritative",
                    }
                elif name == "source_manifest.json":
                    payload = {
                        "source_video_id": "video",
                        "source_content_sha256": "a" * 64,
                        "source_video_integrity_status": "verified",
                    }
                else:
                    payload = {
                        "summary_version": "offline_behavior_summary_v1",
                        "event_count": 0,
                        "events_by_type": {},
                        "events_by_confirmed_member_id": {},
                        "publication_eligible": True,
                    }
                writer.write_json(name, payload)
        for name in jsonl_files:
            if name != omit:
                writer.write_jsonl(name, ())
        if omit != "analyzed_video.mp4":
            writer.write_bytes("analyzed_video.mp4", b"finalized-mp4")


if __name__ == "__main__":
    unittest.main()
