# VLX frontend item-search API document handoff

Date: 2026-10-23
Status: DOCUMENTS DELIVERED / API NOT IMPLEMENTED / NO PUBLIC EXPOSURE

## Confirmed scope

User wants to give a frontend developer an interface document first, for a
page that searches item names and shows a box, name and view score like the
current small-guitar preview. Access will eventually be external, but the
user explicitly deferred deployment questions and requested the document.
Other item names should be accommodated; only the bee and large/small guitar
have user annotations and limited scene verification. Natural-language
location descriptions were not requested.

## Deliverables

`docs/api/vlx-item-search-v1/` contains:

- `README.md`: Chinese frontend contract, explicitly DRAFT/NOT_IMPLEMENTED.
- `openapi.yaml`: six HTTP operations, bearer auth, models and examples;
  WebSocket messages are component schemas plus a documented extension.
- `mock-session.json`: selected small guitar, active session summary.
- `mock-frame.json`: saved real camera-1 JPEG encoded as Base64, with a
  correctly normalized small-guitar rectangle. Time/score fields are mock
  protocol values and are not new inference measurements.

Shareable ZIP:
`D:/project/RetailVisionDev/deliverables/vlx-item-search-docs-20261023/VLX物品搜索接口文档V1_20261023.zip`.
Artifact builder/QA script is outside the repo in the same deliverables folder;
it does not start a model, HTTP server or WebSocket server.

## Contract choices and explicit boundaries

The proposed service uses HTTP catalog/cameras/session/query controls and an
authenticated WebSocket sending per-camera JPEG plus its exact-frame boxes.
Frontend draws labels/boxes and controls layout. Revision invalidation,
stream/frame sequences, independent per-target winners, stale-frame clearing,
decoded-frame ordering and slow-client frame dropping are specified.
Default delivery is at most 10 frames/s/camera, separate from periodic model
confirmation; measured ~3.28s local confirmation is not a public-network SLA.

The current desktop program has no such API and rejects names outside its
three-target alias table. General text querying, dynamic target mapping,
per-user sessions, authentication, control arbitration, serialization and
public HTTPS/WSS deployment remain future implementation work. The document
does not claim these exist. A capability field can disable general queries;
unsupported queries return an explicit error, not a fabricated absence.

The proposed single-active-search limit prevents different browser users
silently replacing one another's search on the one-model GPU. Empty sessions
may continue watching without inference. All example domains/tokens are
placeholders; no camera credentials or private camera URLs are exposed.

The protocol deliberately preserves spaces within general item phrases,
unlike the current desktop alias splitter; this is future API semantics, not
a claim that runtime parsing has already changed. No production API,
identity, database, Offline V1 or Provider A contract is changed.

## Validation

Builder checked all schema references, unique operation IDs, required fields,
types, enums, bounds and all HTTP examples plus mock payloads. The generated
OpenAPI structure also passes the installed FastAPI OpenAPI model parser.
The mock JPEG decodes to its declared 800x448 size; its bbox is in [0,1] with
correct ordering. ZIP contains exactly the four shareable files. This is
document consistency QA, not a running API or external connectivity test.

No live preview files or model services were changed or restarted in this
document-only task. External deployment and real interface implementation
were not performed.
