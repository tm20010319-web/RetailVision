# VLX isolated acceptance handoff

Date: 2026-10-28
Status: COMPLETED / RECIPIENT ACCEPTANCE PENDING / NO DATABASE ACCESS

The user requests an incremental source bundle plus existing structured results
and field/expected-behavior documentation for their own isolated test database.
Do not create or connect to a database, include real credentials/member private
data, or overwrite existing databases. Cloud integration is deferred until their
acceptance. They explicitly selected incremental source, examples and tests.

Inputs are the completed file-only `test_20261028_013957_v9` and
`test_20261028_015125_v8` runs under RetailVisionDev/runtime/vlx-interaction-video.
Both manifests declare `writes_database: false`; current six core source hashes
match their recorded delivery validation. No Git metadata exists in this checkout,
so source is delivered as file snapshots with hashes, not a fabricated Git patch.

Provide native result JSON sanitized by an explicit field allowlist, relative-time
fact JSON (one pickup, holding interval and putdown per completed session), and
stable run-scoped session/user identifiers. No real clock time is inferred from
the filenames. Store/camera database IDs and recording start remain unknown.
Camera display names reflect prior conversation mapping only.

Videos and full-frame imagery are excluded from the distributable package.
Existing anonymous geometric regression fixtures and two inspected object-only
color crops may accompany the source; no membership, face templates or credentials.
Results disclose recorded VLX/pose reuse and fresh SAM3.1 refinement.

## Delivered artifact and validation

Final bundle:
`D:/project/RetailVisionDev/deliverables/vlx-interaction-acceptance-20261028-v1.zip`

SHA256: `fdcfc5b7bf5e3877d2edf170169b0893260ed05f995c19e14e2be892b0dd67b4`

Unpacked sibling directory contains the README, field guide, expected behavior
tables, anonymized native reports, relative-time facts, incremental source files,
configuration examples, test results and a read-only verification script. The
local-only `LOCAL_LOCATIONS.md` is outside the archive. The earlier unversioned
export directory is intermediate scratch; only the `-v1` bundle is delivered.

New `scripts/export_vlx_acceptance.py` is standard-library-only. It validates
session/event agreement, source fingerprints, totals, interval durations and
per-group counts, preserves HOLDING ended_at/duration fields, namespaces session
and anonymous user IDs, and refuses existing or source-nested output directories.
It never connects to a database. Native events include all three event types:
27 events in the first run and 18 in the second, not only pickup/putdown pairs.

Final data contains 15 completed sessions and 45 facts (15 of each type), with
179.778s completed holding. First run: 9 sessions / 119.361s; second: 6 sessions /
60.417s. Null absolute times and deployment IDs are intentional. Expected counts
come from prior source-video review; listed exact times remain algorithm output,
not manually annotated timing ground truth. The final recipient acceptance is
still pending, and no general new-video or cross-camera recall claim is made.

The initial extracted-source check identified a missing test package marker and
two video I/O tests depending on local model config. The bundle now includes the
existing test package support; those two tests use isolated temporary configs
and a detected font. No production config or six recognition core files changed.
The complete suite from the bundled source passes **81 tests**, no skips. Tests
include nine new exchange checks for anonymization, cross-recording uniqueness,
stable re-export IDs, incomplete sessions, inconsistent counts/times and refusing
overwrite. Python syntax checks also pass.

Original source-video and prior annotated/review-video hashes were rechecked
against prior validation; source result hashes remained unchanged. No models or
live previews were touched, and no new full video decode/inference was claimed.
File allowlists, data field allowlists, reviewed object crops, and credential
pattern checks exclude env/database/full-frame media/member records. All 57
payload files plus the checksum manifest are validated; ZIP CRC and each archived
file's SHA256 pass. The bundle is about 1.31 MiB. No server or cloud DB was accessed.
