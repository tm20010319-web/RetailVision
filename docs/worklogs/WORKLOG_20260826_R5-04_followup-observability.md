# Worklog: R5-04 Follow-up Observability and Independent Batches

Date: 2026-08-26  
Status: `COMPLETED / PRODUCTION CONDITIONAL`

## Changes

- Provider SSE errors now emit the stable `provider_stream_error` code and do
  not expose the Provider's raw message.
- Streams that end without `[DONE]` are diagnosed as
  `PROVIDER_STREAM_INCOMPLETE`.
- Malformed tool-call JSON is `STREAM_TOOL_CALL_PARSE_FAILED`; a missing
  function name remains `TOOL_ARGUMENT_ASSEMBLY_FAILED`.
- Acceptance failures are classified as `AGENT_TIMEOUT`,
  `AGENT_PROCESS_FAILED`, `AGENT_OUTPUT_INVALID`, or `MODEL_WRONG_TOOL_CALL`
  where applicable; no acceptance retry is hidden behind these outcomes.
- Every sanitized acceptance record persists the complete `provider_rounds`
  array. Each batch also preserves raw stream JSONL separately.

## Verification

- `OpenAiCompatToolCallingTest`: 10/10; focused Java Provider/Agent/MCP set:
  24 passed, 2 integration skips.
- `test_store_intelligence_agent_acceptance` plus focused MCP suites: 51/51.
- Java package build: passed with `-DskipTests`.
- Full Java: 149 total, 145 passed, 1 known Windows/WSL HookEngine failure,
  3 skipped.
- Independent autonomous batches: 7/7, 6/7, 7/7; 20/21 total, 27 MCP
  calls, 21 invocations, zero retries, zero forbidden-field failures, and one
  retained `compare-toys` grounding failure caused by historical checker
  metric binding.

Evidence root:
`D:\GJJ\work\retailvision_archive\20260826\reports\reports\r5_04_autonomous_mcp_canary_20260826_final3\`.

Production remains conditional because the three-batch gate requires 7/7 in
every batch with zero grounding failures.
