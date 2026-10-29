# RetailVision full source handoff

Date: 2026-10-28 (Asia/Shanghai)
Status: COMPLETED / SOURCE DELIVERY VERIFIED / USER MERGE PENDING

The user selected RetailVision / RealtimeEntryPipeline-master for complete source
delivery after reviewing the distinction from Java Store Agent and Sales Agent.
Deliver the current complete source snapshot, including existing modified files,
dependency manifests/source adjustments, launch scripts, configuration templates,
existing migrations, concise feature/change/startup notes and VLX result location.
Exclude weights, environments, real secrets, databases, video and runtime logs.
No database import, service start, camera access or model inference is authorized
or needed for this packaging task. Preserve original runtime files and sources.

Main workspace and both other application projects lack Git metadata. Do not
fabricate a Git bundle/commit or claim a clean working tree. Original source ZIP
and a complete original-version file hash baseline are unavailable; record this
limit for deleted/renamed-file comparison. Third-party Git metadata does not
substitute for RetailVision history.

Sales Agent and Java Store Agent were not edited in the current VLX work.
Java's supplied snapshot already contains historical STORE-SLIM P1-P4 changes;
do not describe it as historically unmodified. Copies will stay separate.

Build an allowlisted, verified snapshot outside the source root. Clearly record
any packaging-only substitutions (credentials/device placeholders or synthetic
camera examples), preserve full source files and existing migration modules,
and distinguish excluded artifacts from deleted source. Validation and archive
locations will be added when packaging completes.

## Delivery

`D:/project/RetailVisionDev/deliverables/RetailVision-full-source-20261028.zip`

The unpacked sibling directory contains the current RetailVision project (666
source/config/document/static-resource files), 46 VLX-Seek source files, 247 SAM3
and local adapter files, and 490 legacy cam03 analyzer source files. Third-party
weights, installed packages, VCS objects, runtime data and media are excluded.
Existing Python source found in report/data directories is retained separately
from their excluded data. Development Markdown records are documentation, not
runtime logs. Existing migrations are included; the VLX feature adds no migration
and still does not write a database.

Source inventory records original and delivered hashes. Packaging-only changes
replace two real camera demonstrations (static photo and embedded JPEG) with
synthetic examples, replace device identity literals in configs, and clear
notebook outputs/attachments. The source workspace itself is not changed by
those transformations. Portable example configs, explicit-Python launch helpers,
observed environment versions and a separate Windows installation recipe are
provided alongside original startup/config/dependency files. The original
requirements file's local packaging URL is retained for comparison and documented;
the separate recipe replaces it and chooses OpenCV contrib for CSRT.

Validation from the packaged source: all 12 VLX test modules pass **176 tests**,
zero failures/skips, with three existing/dependent deprecation or deliberate
negative-algorithm warnings. All packaged Python files parse. Main-source code
coverage, source inventory hashes and excluded-file/credential checks pass.
Prior anonymous acceptance data remains 15 completed sessions / 45 facts.
No new model inference, camera access, DB migration or cloud write was performed.
No claim of full historical business-suite or clean-machine CUDA installation
validation is made. The pre-existing historical smoke script import of absent
agent.device_event_ingest is documented as an inherited limitation, not repaired
or hidden by packaging. Snapshot/ZIP verification results are recorded in the
package VALIDATION.json, SHA256SUMS.json and adjacent ZIP checksum file.
