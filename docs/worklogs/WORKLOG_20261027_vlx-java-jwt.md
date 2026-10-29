# VLX reuses the existing Java login JWT

Date: 2026-10-27
Status: LIVE / HTTP AND WSS VERIFIED / REAL JAVA LOGIN E2E PENDING

## Request and scope

User requires the existing Java login credential for item search and supplied
the server signing secret. The independent 24-hour development token is retired.
Only VLX transport authentication, startup configuration, tests and frontend
documentation change. No model, tracking, camera, production member API,
database, Offline V1 or Provider A change.

The compatibility reference is
`D:/project/sale-agent-main/app/auth/java_jwt.py` and its contract tests.
That implementation was inspected without importing the Sales application.

## Implementation

- `scripts/vlx_jwt_auth.py` checks a fixed HS256 algorithm, signature, integer
  exp/optional iat, type=front, positive numeric uid (at most 20 ASCII digits),
  and exact string sub equal to normalized uid. JJWT's String key uses STANDARD
  Base64 decoding; neither raw UTF-8 nor hexadecimal key interpretation is used.
- Both HTTP Bearer requests and the first WebSocket authenticate message use
  this verifier. HTTP errors distinguish missing/invalid/expired credentials
  and invalid claims. Established WebSockets recheck expiry and close with 4001;
  initial auth rejection closes with 1008. Existing owner checks use verified uid,
  so refreshed tokens retain the same user's access while foreign users fail.
- `TokenAuth` and the Python issuer were removed. The old PowerShell IssueToken
  switch fails with an explanation. No failed JWT can fall back to a local token.
- `auth_mode: java_jwt` is mandatory. JAVA_FRONT_JWT_SECRET environment config
  takes precedence over the external java_front_jwt_secret_file. Missing,
  malformed or short key configuration fails before stopping the current GPU
  worker. The supplied secret is only in an ACL-restricted external file; it is
  absent from repository sources, tests, handoff documents and logs.
- PyJWT 2.14.0 matches the associated Sales lockfile and is pinned/installed in
  the VLX environment. WebSocket request bound is 16 KiB to accommodate the
  existing 8 KiB JWT contract plus its authentication envelope.
- Interface version is 1.1.0. Repository README/OpenAPI/frontend env example
  and the user's desktop Markdown handoff now describe the original login JWT.
  No actual JWT or secret is embedded in these files.

## Verification

92 focused tests passed: JWT key/algorithm and claim errors, required claims,
startup config failure, no legacy fallback, HTTP header compatibility, foreign
session read/mutation/deletion rejection, token refresh with stable ownership,
WebSocket initial auth rejection and mid-connection expiry, plus existing
session/revision/clear/frame synchronization, tracking, camera recovery and
view-selection regressions. Warnings are existing Starlette/SQLAlchemy
deprecations and the deliberately rejected HS512 test's key-length warning.

The API was gracefully restarted and the desktop preview released the GPU.
The original Funnel URL is unchanged. Sanitized live evidence is saved outside
the repository at
`D:/project/RetailVisionDev/runtime/vlx-item-search-api/java_jwt_acceptance_20261027.json`.

Local and public health were ready; authenticated catalog returned 200,
unauthenticated/expired/legacy tokens returned 401, foreign owner returned 403,
localhost:5173 CORS passed, public WSS delivered two live camera streams and
cleared annotations for both cameras. Refreshed same-user token worked;
foreign and initially expired WSS were rejected with 1008, and established
connection expiry closed with 4001. Temporary test sessions were deleted.

A separate public WSS observation exercised completed VLX inference on BOTH
cameras with a small-guitar query. No positive annotation was observed in that
25-second current-scene window; this is not a positive detection or accuracy
qualification. Both inference paths completed and the test session was removed.

Tests used short-lived synthetic JWTs constructed in memory with the configured
secret. No real Java login was performed and no Python user account was created.
This validates the deployed verifier/transport, not the external issuer E2E.
Local signature checks do not add immediate logout revocation or per-camera
entitlements. Existing recognition/scene qualification limitations remain.

PyJWT API reference consulted for fixed-algorithm decode and required claims:
https://pyjwt.readthedocs.io/en/stable/api.html .
