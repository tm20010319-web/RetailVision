"""Versioned, append-only import snapshot for operating measurements.

The seeder/importer is the writer. MCP opens a completed snapshot read-only.
Measurements contain additive components, never prewritten answers or ratios.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE dataset (
    id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
    source_kind TEXT NOT NULL, generated_at TEXT NOT NULL,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, timezone TEXT NOT NULL,
    seed INTEGER NOT NULL, description TEXT NOT NULL
);
CREATE TABLE stores (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL,
    floor_area REAL NOT NULL CHECK(floor_area>0), invested_cents INTEGER NOT NULL
);
CREATE TABLE measurements (
    id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES dataset(id),
    store_id TEXT NOT NULL REFERENCES stores(id), business_date TEXT NOT NULL,
    metric TEXT NOT NULL, dimensions TEXT NOT NULL, value REAL NOT NULL,
    UNIQUE(dataset_id,store_id,business_date,metric,dimensions)
);
CREATE INDEX measurements_lookup ON measurements(store_id,metric,business_date);
CREATE TABLE metric_coverage (
    metric TEXT PRIMARY KEY, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    absence_means_zero INTEGER NOT NULL CHECK(absence_means_zero IN (0,1))
);
CREATE TABLE entities (
    store_id TEXT NOT NULL REFERENCES stores(id), entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, attributes TEXT NOT NULL,
    PRIMARY KEY(store_id,entity_type,entity_id)
);
"""


def json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def open_readonly(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve(strict=True)
    connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        row = connection.execute("SELECT * FROM dataset").fetchall()
        if len(row) != 1 or row[0]["schema_version"] != SCHEMA_VERSION:
            raise ValueError("business_schema_incompatible")
        connection.execute("SELECT metric FROM metric_coverage LIMIT 1").fetchone()
        connection.execute("SELECT id FROM measurements LIMIT 1").fetchone()
    except Exception:
        connection.close()
        raise
    return connection
