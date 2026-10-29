# Offline Behavior + Identity Foundation V1 Module Mapping

This document records the logical ownership of the V1 seams without authorizing
physical package migration.

| Current file | Logical owner | Layer | Durable write |
| --- | --- | --- | --- |
| `agent/offline_media.py` | Evidence/media timeline | Domain | None |
| `agent/offline_source.py` | Evidence/source provenance + Identity registration scope | Domain/application | None |
| `agent/offline_recognition_provider.py` | Identity/recognition | Adapter/application | None |
| `agent/offline_identity.py` | Identity/historical resolution + Track aggregation | Domain/application + persistence port | None |
| `agent/offline_identity_sql.py` | Identity/historical revision persistence | SQL adapter | Mapping revision and Live current projection in one transaction |
| `agent/offline_identity_association.py` | Identity/track association | Domain adapter | None |
| `agent/offline_behavior.py` | Behavior/merchandise normalization and safe identity projection | Domain/application | None |
| `agent/offline_artifacts.py` | Evidence/restricted run artifacts | Adapter | Immutable files and manifest |
| `agent/offline_publication.py` | Behavior/publication | Application | Publication fence + BehaviorEvent batch |
| `agent/offline_publication_sql.py` | Behavior/publication | SQL adapter | Publication fence + BehaviorEvent batch in one transaction |
| `agent/offline_video_models.py` | Offline analysis attempt | Application contract | None |
| `pipelines/offline_behavior_pipeline.py` | Runtime/application composition | Runtime | Through injected ports only |
| `scripts/run_offline_behavior_analysis.py` | Offline operator entry | CLI adapter | Through application interfaces |

The only V1 schema additions are `OfflineBehaviorPublication` and
`DeviceMemberIdentityRevision`. `OfflineTrackIdentityAssociation` remains a
sealed restricted artifact, not an ORM fact.
