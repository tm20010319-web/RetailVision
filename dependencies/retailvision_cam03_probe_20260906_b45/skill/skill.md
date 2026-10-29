---
name: store-vision-agent-refactor
description: Use when refactoring the YOLO/OpenCV/LLM/Agent store customer vision analysis project, especially when implementing phased RealtimeEntryPipeline, CustomerProfilePipeline, HistoricalVideoAnalysisPipeline with verification gates after each phase.
---

# Store Vision Agent Refactor

## Core Rules

Work in three phases. Do not implement the next phase until the current phase has been tested and reported.

Phase 1: RealtimeEntryPipeline
- Implement only YOLO + OpenCV + rule-based entry detection.
- Do not call any LLM service.
- Reuse existing VisionDetector.
- Detect new customer entry by stable track_id.
- Trigger CustomerProfilePipeline only through a clearly isolated interface or mock callback.
- Validate with import test, unit/smoke test, and no-LLM assertion.

Phase 2: CustomerProfilePipeline
- Receive track_id, frame, bbox, camera_id, zone_name.
- Crop person bbox with OpenCV.
- Save crop image.
- Call MockVisionLLMService.
- Validate output with Pydantic CustomerProfileSchema.
- Save through mock repository/service interface.
- Test crop generation, schema validation, and malformed LLM output rejection.

Phase 3: HistoricalVideoAnalysisPipeline
- Accept video path or time range.
- Sample frames by process_every_seconds, default 3 seconds.
- Run VisionDetector.
- Generate zone_event.
- Deduplicate zone visits by track_id + zone_name.
- Aggregate with VideoAggregator.
- Send only aggregated summary to MockTextLLMService.
- Validate report with Pydantic InsightReportSchema.
- Test frame sampling, detection dict conversion, zone deduplication, aggregation, and schema validation.

## Global Architecture Rules

- Do not send raw YOLO per-frame detections to LLM.
- LLM is called only for customer profile image analysis or business report generation.
- Detection dataclass must be converted to dict before pipeline outputs, logs, aggregation, or persistence.
- Prefer MockLLMService first. Do not integrate real LLM unless explicitly requested.
- Keep changes scoped. Avoid large rewrites of legacy modules.
- Preserve detector.py YOLO capability.
- If existing backend package imports are incomplete, use local imports or clearly report required package layout.

## Verification Gate

After each phase, stop and report:
- Files changed
- Tests/checks run
- Key passing evidence
- Remaining risk
- Whether user approval is needed before continuing

Do not continue to the next phase until the user says to proceed.