# Product Analytics Timestamp Contract Note

## Current behavior

Product Analytics currently stores and queries naive local timestamps. MCP input
schemas therefore accept naive ISO-8601 values, and the caller must use the store's
configured local time when selecting a window. A timezone-qualified value is rejected
by the current adapter rather than silently converted.

This note records operational debt only. It does not change the database schema,
existing rows, aggregation semantics, or Offline V1 behavior.

## Future contract

- **Storage:** persist event and fact timestamps as UTC.
- **Display and query input:** convert to and present in the store timezone.
- **Boundary:** define one explicit conversion at the API/MCP boundary, preserving
  half-open `[start, end)` windows.

Migration requires a separately approved schema/data contract change and timezone
backfill plan.
