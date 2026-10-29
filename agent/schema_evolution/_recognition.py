from __future__ import annotations

import hashlib

from ._common import add_missing_columns, logger


def evolve_member_arrival(connection, columns: set[str] | None) -> None:
    if columns is not None and "snapshot_path" not in columns:
        add_missing_columns(connection, "member_entry_events", columns, {
            "snapshot_path": "VARCHAR(512)",
        })


def evolve_track_associations(connection, columns: set[str] | None) -> None:
    if columns is not None:
        add_missing_columns(connection, "track_association_evaluations", columns, {
            "mode": "VARCHAR(16) NOT NULL DEFAULT 'shadow'",
            "assessment_fingerprint": "VARCHAR(64) NOT NULL DEFAULT ''",
            "store_id": "VARCHAR(64)",
            "runtime_instance_id": "VARCHAR(64)",
            "runtime_owner_id": "VARCHAR(128)",
            "runtime_key": "VARCHAR(128)",
            "excluded_candidates": "JSON NOT NULL DEFAULT '[]'",
            "candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "effective_occurred_at": "DATETIME",
            "watermark_at": "DATETIME",
            "window_complete": "BOOLEAN NOT NULL DEFAULT 1",
            "policy_snapshot": "JSON NOT NULL DEFAULT '{}'",
            "spatial_evidence": "JSON",
        })
        connection.exec_driver_sql(
            "UPDATE track_association_evaluations SET assessment_fingerprint = "
            "COALESCE(NULLIF(assessment_fingerprint, ''), id), "
            "excluded_candidates = COALESCE(excluded_candidates, '[]'), "
            "candidate_count = COALESCE(candidate_count, 0), "
            "window_complete = COALESCE(window_complete, 1), "
            "policy_snapshot = COALESCE(policy_snapshot, '{}')"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_track_association_evaluation_assessment ON "
            "track_association_evaluations(recognition_event_id, assessment_fingerprint)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_track_association_evaluation_event "
            "ON track_association_evaluations(recognition_event_id, created_at)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_track_association_evaluation_runtime "
            "ON track_association_evaluations(camera_id, runtime_instance_id, observed_at)"
        )


def evolve_track_identity_bindings(
    connection,
    binding_columns: set[str] | None,
    finalization_job_columns: set[str] | None,
) -> None:
    """Additive Phase 2B schema evolution with fail-fast active-binding fencing."""
    if binding_columns is not None:
        add_missing_columns(connection, "track_identity_bindings", binding_columns, {
            "expires_at": "DATETIME",
        })
        _create_track_identity_binding_indexes(connection)
    if finalization_job_columns is not None:
        add_missing_columns(
            connection,
            "track_association_finalization_jobs",
            finalization_job_columns,
            {
                "application_status": "VARCHAR(24) NOT NULL DEFAULT 'pending'",
                "application_reason": "VARCHAR(256)",
            },
        )
        connection.exec_driver_sql(
            "UPDATE track_association_finalization_jobs SET application_status = "
            "CASE WHEN application_status IS NULL OR application_status = '' THEN "
            "CASE WHEN status = 'succeeded' AND binding_id IS NOT NULL THEN 'applied' "
            "ELSE 'pending' END ELSE application_status END"
        )
        connection.exec_driver_sql(
            "UPDATE track_association_finalization_jobs SET application_status = 'applied' "
            "WHERE application_status = 'pending' "
            "AND status IN ('binding_committed', 'succeeded') "
            "AND binding_id IS NOT NULL"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_track_finalization_job_recognition_status "
            "ON track_association_finalization_jobs(recognition_event_id, status)"
        )


def _create_track_identity_binding_indexes(connection) -> None:
    legacy_status_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM track_identity_bindings WHERE status = 'active'"
    ).scalar_one()
    if legacy_status_count:
        logger.error(
            "Schema migration conflict: legacy TrackIdentityBinding status rows "
            "count=%s; explicit repair is required",
            legacy_status_count,
        )
        raise RuntimeError("track_identity_binding_legacy_active_status")
    conflicts = connection.exec_driver_sql(
        "SELECT track_token, COUNT(*) "
        "FROM track_identity_bindings "
        "WHERE status = 'confirmed' AND valid_to IS NULL AND track_token IS NOT NULL "
        "GROUP BY track_token "
        "HAVING COUNT(*) > 1"
    ).all()
    if conflicts:
        logger.error(
            "Schema migration conflict: duplicate unterminated confirmed "
            "TrackIdentityBinding "
            "keys=%s; active unique index was not created",
            [
                {
                    "track_token_hash": hashlib.sha256(
                        str(row[0]).encode("utf-8")
                    ).hexdigest()[:12],
                    "count": row[1],
                }
                for row in conflicts
            ],
        )
        raise RuntimeError("track_identity_binding_active_duplicate")
    connection.exec_driver_sql(
        "DROP INDEX IF EXISTS uq_track_identity_binding_active_token"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_track_identity_binding_active_token ON track_identity_bindings(track_token) "
        "WHERE status = 'confirmed' AND valid_to IS NULL"
    )


def evolve_push_coverage(connection, columns: set[str] | None) -> None:
    """Apply additive schema support for immutable Push coverage observations."""
    if columns is None:
        return
    add_missing_columns(connection, "push_coverage_observations", columns, {
        "observation_key": "VARCHAR(256)",
        "device_id": "VARCHAR(64)",
        "observation_type": "VARCHAR(32)",
        "observed_at": "DATETIME",
        "device_event_at": "DATETIME",
        "receiver_instance_id": "VARCHAR(64)",
        "boot_id": "VARCHAR(64)",
        "source_event_id": "VARCHAR(128)",
        "canonical_event_id": "VARCHAR(64)",
        "outcome": "VARCHAR(24)",
        "failure_class": "VARCHAR(64)",
        "policy_version": "VARCHAR(64)",
        "device_metadata": "JSON",
        "created_at": "DATETIME",
    })
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_push_coverage_observation_key "
        "ON push_coverage_observations(observation_key)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_push_coverage_observation_device_time "
        "ON push_coverage_observations(device_id, observed_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_push_coverage_observation_instance_time "
        "ON push_coverage_observations(receiver_instance_id, observed_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_push_coverage_observation_type_time "
        "ON push_coverage_observations(device_id, observation_type, observed_at)"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS push_coverage_observations_no_update "
        "BEFORE UPDATE ON push_coverage_observations "
        "BEGIN SELECT RAISE(ABORT, 'push coverage observations are immutable'); END"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS push_coverage_observations_no_delete "
        "BEFORE DELETE ON push_coverage_observations "
        "BEGIN SELECT RAISE(ABORT, 'push coverage observations are immutable'); END"
    )


def evolve_inbox(
    connection,
    columns: set[str] | None,
    *,
    preserve_existing_rows: bool = False,
) -> None:
    if columns is None:
        return
    add_missing_columns(connection, "device_recognition_inbox", columns, {
        "claim_token": "VARCHAR(36)",
        "device_occurred_at_raw": "VARCHAR(128)",
        "device_utc_offset_minutes": "INTEGER",
        "receiver_arrived_at": "DATETIME",
        "future_skew_seconds": "FLOAT",
        "face_spatial_evidence": "JSON",
        "device_metadata": "JSON",
        "background_capture_path": "VARCHAR(512)",
        "background_capture_sha256": "VARCHAR(64)",
        "background_capture_width": "INTEGER",
        "background_capture_height": "INTEGER",
        "store_id": "VARCHAR(64)",
        "association_status": "VARCHAR(24)",
        "recognition_status": "VARCHAR(32)",
        "store_resolution_status": "VARCHAR(24)",
        "track_association_status": "VARCHAR(32)",
        "runtime_instance_id": "VARCHAR(64)",
        "evaluation_id": "VARCHAR(64)",
        "finalization_job_id": "VARCHAR(64)",
        "binding_id": "VARCHAR(64)",
        "track_association_reason": "VARCHAR(256)",
        "association_outcome": "VARCHAR(32)",
        "matched_entry_event_id": "VARCHAR(64)",
        "matched_visit_id": "VARCHAR(64)",
        "matched_track_token": "VARCHAR(256)",
        "match_reason": "VARCHAR(128)",
        "match_confidence": "FLOAT",
        "associated_at": "DATETIME",
        "association_claim_token": "VARCHAR(64)",
        "association_locked_at": "DATETIME",
        "association_claim_expires_at": "DATETIME",
        "association_attempts": "INTEGER",
        "association_next_retry_at": "DATETIME",
        "association_deadline_at": "DATETIME",
        "association_last_error": "VARCHAR(512)",
        "store_mapping_claim_token": "VARCHAR(64)",
        "store_mapping_locked_at": "DATETIME",
        "store_mapping_claim_expires_at": "DATETIME",
        "store_mapping_attempts": "INTEGER",
        "store_mapping_last_attempt_at": "DATETIME",
        "store_mapping_next_retry_at": "DATETIME",
        "store_mapping_wait_deadline_at": "DATETIME",
        "store_mapping_last_error": "VARCHAR(512)",
        "realtime_effect_status": "VARCHAR(24)",
        "realtime_effect_reason": "VARCHAR(128)",
        "projection_pending": "BOOLEAN",
    })
    if preserve_existing_rows:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_device_recognition_inbox_source_event "
            "ON device_recognition_inbox(device_id, source, source_event_id)"
        )
        _create_inbox_indexes(connection)
        return
    association_value = (
        "CASE WHEN status IN ('ignored', 'unmapped') THEN 'expired' "
        "ELSE 'pending' END"
        if "status" in columns
        else "'pending'"
    )
    connection.exec_driver_sql(
        "UPDATE device_recognition_inbox SET association_status = "
        f"{association_value} WHERE association_status IS NULL"
    )
    if "status" in columns and "member_id" in columns:
        recognition_value = (
            "CASE WHEN status IN ('ignored', 'unmapped') THEN 'unmapped' "
            "WHEN member_id IS NULL THEN 'pending_member_mapping' ELSE 'accepted' END"
        )
    elif "member_id" in columns:
        recognition_value = (
            "CASE WHEN member_id IS NULL THEN 'pending_member_mapping' ELSE 'accepted' END"
        )
    else:
        recognition_value = "'pending_member_mapping'"
    connection.exec_driver_sql(
        "UPDATE device_recognition_inbox SET recognition_status = "
        f"{recognition_value} WHERE recognition_status IS NULL"
    )
    connection.exec_driver_sql(
        "UPDATE device_recognition_inbox SET store_resolution_status = "
        "COALESCE(store_resolution_status, 'missing')"
    )
    connection.exec_driver_sql(
        "UPDATE device_recognition_inbox SET track_association_status = "
        "COALESCE(track_association_status, 'pending')"
    )
    connection.exec_driver_sql(
        "UPDATE device_recognition_inbox SET association_outcome = "
        "COALESCE(association_outcome, 'pending')"
    )
    connection.exec_driver_sql(
        "UPDATE device_recognition_inbox SET association_attempts = "
        "COALESCE(association_attempts, 0), projection_pending = "
        "COALESCE(projection_pending, 0)"
    )
    connection.exec_driver_sql(
        "UPDATE device_recognition_inbox SET store_mapping_attempts = "
        "COALESCE(store_mapping_attempts, 0), "
        "store_mapping_wait_deadline_at = COALESCE("
        "store_mapping_wait_deadline_at, datetime(occurred_at, '+600 seconds'))"
    )
    _create_inbox_indexes(connection)
    repair_duplicate_source_events(connection)
    connection.exec_driver_sql(
        "DROP INDEX IF EXISTS idx_device_recognition_inbox_source_event"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_device_recognition_inbox_source_event "
        "ON device_recognition_inbox(device_id, source, source_event_id)"
    )


def _create_inbox_indexes(connection) -> None:
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_device_recognition_track_dispatch ON "
        "device_recognition_inbox(store_id, recognition_status, "
        "store_resolution_status, track_association_status, association_next_retry_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_device_recognition_track_deadline ON "
        "device_recognition_inbox(track_association_status, association_deadline_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_device_recognition_finalization_job ON "
        "device_recognition_inbox(finalization_job_id)"
    )


def repair_duplicate_source_events(connection) -> None:
    duplicate_keys = connection.exec_driver_sql(
        "SELECT device_id, source, source_event_id "
        "FROM device_recognition_inbox "
        "GROUP BY device_id, source, source_event_id HAVING COUNT(*) > 1"
    ).all()
    for device_id, source, source_event_id in duplicate_keys:
        canonical_ids = connection.exec_driver_sql(
            "SELECT canonical_event_id FROM device_recognition_inbox "
            "WHERE device_id = ? AND source = ? AND source_event_id = ? "
            "ORDER BY received_at IS NULL, received_at, canonical_event_id",
            (device_id, source, source_event_id),
        ).scalars().all()
        for canonical_id in canonical_ids[1:]:
            connection.exec_driver_sql(
                "UPDATE device_recognition_inbox SET source_event_id = ? "
                "WHERE canonical_event_id = ?",
                (f"legacy-duplicate:{canonical_id}", canonical_id),
            )
