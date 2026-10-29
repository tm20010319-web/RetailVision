# VLX authenticated public frontend integration service

Date: 2026-10-23
Status: LIVE FUNNEL / HTTP AND WSS CHECKED / RECOGNITION QUALIFICATION LIMITED

## Authorization and scope

After the document-only handoff, user explicitly requested implementing and
exposing the service for external frontend integration, selected Tailscale,
and confirmed the frontend must not install a client. Official Funnel docs
were consulted. Tailscale 1.104.2 was installed from the official signed
winget package; its existing device identity connected. User completed the
account-level Funnel enablement in the opened browser and confirmed it.

This is the standalone preview adapter, not the production member API,
identity chain, Offline V1, Provider A or a database change. Original YOLO
and standalone desktop entry scripts remain. The previous active VLX GUI
was gracefully stopped to give the single GPU/model to the API worker.

## Entry and current public service

- Start: `scripts/start_vlx_item_search_api.ps1`.
- Program: `scripts/run_vlx_item_search_api.py`.
- HTTP/WS adapter: `scripts/vlx_item_search_api.py`.
- Local IPC: `scripts/vlx_api_bridge.py`.
- Configuration: `config/vlx_item_search_api.yaml`.
- Publish/stop Funnel: `scripts/publish_vlx_item_search_funnel.ps1` (`-Stop`).
- Local bind: `http://127.0.0.1:8787`, no LAN-wide bind.
- Public root: `https://desktop-qaer92d.tail173be1.ts.net`.
- Swagger: `/docs`; API base `/api/v1/item-search`;
  WS `/ws/v1/item-search/sessions/{session_id}`.

Funnel background config has HTTPS 443, root proxy to 127.0.0.1:8787 and
AllowFunnel=true. It stays configured until explicitly disabled, rather than
being a URL with an automatic expiry. Computer, Tailscale and API must stay on.
Default frontend origins are localhost/127.0.0.1 on 5173 and 3000. Other
frontend origins need the configured allowlist updated and service restarted.

## Implementation

Six HTTP operations implement the documented catalog, cameras, create/get/
delete session, and query replacement. Short-lived 24-hour opaque bearer
credentials are checked on HTTP and each WS loop; only hashes are stored in
the credential registry. The plaintext handoff file is outside the repo and
shareable document ZIP, with user/SYSTEM-only Windows directory permissions.
Token rotation invalidates old tokens without model restart. Credentials are
never printed in command lines, test output, access logs or worklogs.

Per-owner opaque sessions, 30-minute idle expiry, four WS connections/owner,
eight sessions/owner, a global 32-session bound, one active GPU search, atomic
control revisions, conflicting-request rejection and stale-result rejection
are implemented. Unauthenticated requests cannot read catalog/cameras/frames;
foreign owners cannot control/read another owner's session. Identical query
targets do not restart model work. Clear leaves live video but no annotation.

The existing dual-camera worker has optional file-control/frame-publishing
flags; ordinary desktop behavior is unchanged without them. The publisher
atomically exports the raw JPEG and its frame-aligned winning annotations,
with normalized geometry and separate source/model frame sequences. HTTP
summaries contain no tempting stale overlay coordinates. WS sends latest
frames, not a backlog, and checks query revision before sending. Stale camera
frames are not emitted, and API/WS state reports degradation. Slow sends time
out. Sender preview FPS is measured per connection, separately from decoder
and inference speed. This is an integration implementation, not a load test.

Dynamic text targets preserve spaces, support Chinese/English using the
vendor's bilingual task templates, and map exact returned category labels to
opaque target IDs. Known three targets use existing tested phrases; other
names have validation_status=unverified. No new annotation dataset was used
to fine-tune VLX, and no calibrated target-confidence claim is introduced.

## Verification

44 focused tests pass (existing SQLAlchemy and Starlette/httpx deprecation
warnings). New real ASGI HTTP/WS tests cover auth, owner boundaries, atomic
one-GPU selection, expected revisions, clear/reject stale packets, expiration,
JPEG dimensions and normalized bbox, generic phrases and unknown target
mapping, camera staleness and model-not-ready behavior. Previous 33 preview
tests still pass. No full repository qualification claim.

A later restart exposed a transient camera-1 identity HTTP timeout: ports
remained reachable and a repeated pinned identity check succeeded, but the
previous startup-only preflight left that camera permanently unconnected.
`vlx_camera_recovery.py` now retries missing cameras every ten seconds while
requiring the same identity verification before starting a source. API mode
can stay up when both sources are temporarily absent; offline status is
explicit. Two regressions prove timeout recovery and identity-mismatch
rejection. Both real identity checks passed before the final restart.

Actual local live check evidence is under
`D:/project/RetailVisionDev/runtime/vlx-item-search-api/`:

- `live_check_results.json`: small guitar, large guitar, both, Chinese 鼠标,
  then empty query. All requested guitar categories were returned; the mouse
  query selected the actual white mouse on camera 1. The raw camera image
  and returned coordinates were visually checked. This one mouse result is
  not general object accuracy qualification.
- Search-to-first-box times were about 11.70s, 1.48s, 2.98s and 5.63s in
  this sequence; initial camera-1 staleness delayed the first case. Clear
  produced empty frames from both cameras in ~0.09s. These are observations,
  not a hard SLA or the earlier 3.28s mean-confirmation benchmark.
- `public_check_results.json`: HTTPS health 200, no-token catalog 401,
  authenticated catalog 200, allowed CORS preflight 200, WSS live JPEG plus
  small-guitar bbox, then empty annotations on the cleared revision. First
  public box was received ~1.16s after request in the first check; the final
  post-recovery domain retest took ~2.36s and also verified clearing.
- Public Swagger was opened and its six operations visibly inspected in Edge.
- Final same-origin public WSS check received fresh empty-query frames from
  BOTH cameras after the recovery change and left no active search session.

HTTP/WSS tests were made from this host using its configured network path.
An independent frontend developer network has not been observed. Direct
no-proxy curl timed out in the local environment, while HTTPS/WSS with the
configured network path and Edge succeeded; this was not hidden as an
independent-network test. Funnel CLI confirms public sharing enabled.

Documentation and ZIP were updated from draft to implemented service,
including actual endpoints, a frontend env example, token handling and
start/stop commands. The ZIP contains five frontend files and no actual Token.
The old draft worklog remains historical. Recognition of arbitrary
positions/classes, similar physical instances, simultaneous multi-user GPU
searches, external bandwidth and long-running operation remain unqualified.

Follow-up documentation-only request: added a front-loaded Chinese success
response quick reference to the Markdown handoff, with HTTP 200 and WS frame
JSON excerpts, field meanings and the per-camera empty-annotation rule.
Examples explicitly mark omitted fields/Base64 placeholders and distinguish
request acceptance from a found result. No API/model service changes or
restart were needed; JSON fences were parsed and the handoff ZIP refreshed.
