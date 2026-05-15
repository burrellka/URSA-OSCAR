-- URSA-OSCAR DuckDB schema v1
-- Per Design v1.1 § Data Model (refined for DuckDB from v1.0 framework).
-- Single-writer pattern: only the API container executes this.
-- Migrations live in migrations.py; this file is the initial snapshot.

CREATE TABLE IF NOT EXISTS nightly_summary (
    date DATE PRIMARY KEY,
    session_count INTEGER,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    total_time_minutes INTEGER,
    total_ahi DOUBLE,
    obstructive_ahi DOUBLE,
    central_ahi DOUBLE,
    hypopnea_index DOUBLE,
    rera_index DOUBLE,
    median_pressure DOUBLE,
    p95_pressure DOUBLE,
    p995_pressure DOUBLE,
    median_epap DOUBLE,
    p95_epap DOUBLE,
    p995_epap DOUBLE,
    median_leak DOUBLE,
    p95_leak DOUBLE,
    p995_leak DOUBLE,
    minutes_in_apnea INTEGER,
    minutes_over_leak_redline DOUBLE,
    cheyne_stokes_pct DOUBLE,
    large_leak_pct DOUBLE,
    machine_model VARCHAR,
    mode VARCHAR,
    min_pressure_setting DOUBLE,
    max_pressure_setting DOUBLE,
    epr_level INTEGER,
    ramp_time_minutes INTEGER,
    humidity_level VARCHAR,
    mask_type VARCHAR,
    -- Schema v2 (2026-05-13): Device-Settings expansion for OSCAR-parity
    -- Daily View. Sourced from SETTINGS/CurrentSettings.json. Same per-night-
    -- accuracy caveat as the v1 settings: the JSON reflects the most-recent
    -- export-time state, not per-night history (STR.edf parsing lands in
    -- Phase 4 to refine).
    antibacterial_filter VARCHAR,    -- "Yes" / "No"
    climate_control VARCHAR,         -- "Auto" / "Manual"
    epr_mode VARCHAR,                -- "Full Time" / "Ramp Only" / "Off"
    humidifier_status VARCHAR,       -- "On" / "Off"
    patient_view VARCHAR,            -- "Full" / "Limited" / "Off"
    response_mode VARCHAR,           -- "Soft" / "Standard" (derived from AutoSetComfort)
    smart_start VARCHAR,             -- "On" / "Off"
    temperature_celsius DOUBLE,      -- Heated tube setpoint, °C
    temperature_enable VARCHAR,      -- "On" / "Off" / "Auto"
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Schema v2 migration for existing v1 databases. DuckDB supports
-- IF NOT EXISTS on ADD COLUMN, so re-running these is safe.
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS antibacterial_filter VARCHAR;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS climate_control VARCHAR;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS epr_mode VARCHAR;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS humidifier_status VARCHAR;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS patient_view VARCHAR;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS response_mode VARCHAR;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS smart_start VARCHAR;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS temperature_celsius DOUBLE;
ALTER TABLE nightly_summary ADD COLUMN IF NOT EXISTS temperature_enable VARCHAR;

-- Sequence MUST be created before nightly_events so the DEFAULT can reference it.
CREATE SEQUENCE IF NOT EXISTS nightly_events_id_seq START 1;
CREATE TABLE IF NOT EXISTS nightly_events (
    -- Phase 3 Item 1A: id allocation lives inside the INSERT transaction
    -- via DEFAULT nextval(). Eliminates the pre-fetch race where a
    -- Python loop pulled IDs out of the sequence and a later INSERT
    -- could fail mid-batch, leaving the sequence advanced past the
    -- highest committed row and producing later "Duplicate key" errors.
    id BIGINT PRIMARY KEY DEFAULT nextval('nightly_events_id_seq'),
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    session_id INTEGER,
    event_type VARCHAR NOT NULL,
    duration_seconds DOUBLE,
    pressure_at_event DOUBLE,
    epap_at_event DOUBLE,
    flow_at_event DOUBLE,
    leak_at_event DOUBLE
);
-- Schema-v2-to-v3 migration: existing v1/v2 tables had `id BIGINT PRIMARY KEY`
-- with no DEFAULT. ALTER COLUMN SET DEFAULT brings them in line.
ALTER TABLE nightly_events ALTER COLUMN id SET DEFAULT nextval('nightly_events_id_seq');
CREATE INDEX IF NOT EXISTS idx_events_date ON nightly_events(date);
CREATE INDEX IF NOT EXISTS idx_events_type ON nightly_events(event_type);

CREATE TABLE IF NOT EXISTS pressure_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    pressure DOUBLE,
    epap DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS flow_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    flow_rate DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS leak_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    leak_rate DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS flow_limit_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    flow_limit DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS tidal_volume_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    tidal_volume DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS minute_vent_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    minute_vent DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS resp_rate_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    resp_rate DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS snore_timeseries (
    date DATE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    snore DOUBLE,
    PRIMARY KEY (date, timestamp)
);

CREATE SEQUENCE IF NOT EXISTS manual_logs_id_seq START 1;
CREATE TABLE IF NOT EXISTS manual_logs (
    -- Phase 3 Item 1A: same DEFAULT nextval() discipline as nightly_events.
    id BIGINT PRIMARY KEY DEFAULT nextval('manual_logs_id_seq'),
    date DATE NOT NULL,
    log_type VARCHAR NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    value_text VARCHAR,
    value_numeric DOUBLE,
    unit VARCHAR,
    category VARCHAR,
    notes VARCHAR,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE manual_logs ALTER COLUMN id SET DEFAULT nextval('manual_logs_id_seq');
CREATE INDEX IF NOT EXISTS idx_manual_logs_date ON manual_logs(date);
CREATE INDEX IF NOT EXISTS idx_manual_logs_type ON manual_logs(log_type);
CREATE INDEX IF NOT EXISTS idx_manual_logs_category ON manual_logs(category);

CREATE TABLE IF NOT EXISTS config (
    key VARCHAR PRIMARY KEY,
    value VARCHAR,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE IF NOT EXISTS import_log_id_seq START 1;
CREATE TABLE IF NOT EXISTS import_log (
    -- Phase 3 Item 1A: same DEFAULT nextval() discipline.
    id BIGINT PRIMARY KEY DEFAULT nextval('import_log_id_seq'),
    import_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_path VARCHAR,
    nights_imported INTEGER,
    earliest_date DATE,
    latest_date DATE,
    status VARCHAR,
    error_message VARCHAR
);
ALTER TABLE import_log ALTER COLUMN id SET DEFAULT nextval('import_log_id_seq');

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description VARCHAR
);

-- Phase 4 Ticket 1 (v4, 2026-05-14) — session-level data + exclusion.
--
-- `sessions` is the canonical per-session record (one row per non-empty
-- session per night), written by the importer alongside nightly_summary.
-- It's the join key for recompute_summary() when excluding sessions
-- from a night's stats. Backfill on first 0.7.0 deploy derives rows
-- from nightly_events' (date, session_id) groupings + min/max of
-- their event timestamps (an underestimate of true mask-on, but good
-- enough until the next re-import refreshes it from EDF).
CREATE TABLE IF NOT EXISTS sessions (
    date DATE NOT NULL,
    session_id INTEGER NOT NULL,
    start_ts TIMESTAMP NOT NULL,
    end_ts TIMESTAMP NOT NULL,
    mask_on_minutes DOUBLE NOT NULL,
    -- v6 (Phase 5.5) — per-session pressure-stat cache. Populated by
    -- the importer for new imports + by the v6 auto-backfill in
    -- apply_migrations() for existing rows. NULL on IPAP columns is
    -- expected on single-pressure devices (AirSense 11 etc.) where
    -- URSA doesn't track a separate IPAP channel — the columns exist
    -- for future bilevel device support.
    pressure_median   DOUBLE DEFAULT NULL,
    pressure_p95      DOUBLE DEFAULT NULL,
    pressure_p995     DOUBLE DEFAULT NULL,
    ipap_median       DOUBLE DEFAULT NULL,
    ipap_p95          DOUBLE DEFAULT NULL,
    ipap_p995         DOUBLE DEFAULT NULL,
    epap_median       DOUBLE DEFAULT NULL,
    epap_p95          DOUBLE DEFAULT NULL,
    epap_p995         DOUBLE DEFAULT NULL,
    flow_limit_median DOUBLE DEFAULT NULL,
    flow_limit_p95    DOUBLE DEFAULT NULL,
    flow_limit_p995   DOUBLE DEFAULT NULL,
    leak_median       DOUBLE DEFAULT NULL,
    leak_p95          DOUBLE DEFAULT NULL,
    leak_p995         DOUBLE DEFAULT NULL,
    PRIMARY KEY (date, session_id)
);

-- v6 migration ALTERs: bring 0.7.0-0.9.7 databases up to v6 column
-- shape. CREATE TABLE IF NOT EXISTS above is a no-op for them; these
-- ALTERs fill the gap. DuckDB ALTER TABLE ADD COLUMN IF NOT EXISTS is
-- supported since 0.7.0; per ADR-003 we pin DuckDB 0.10+, so this is
-- safe. Idempotent on fresh DBs — the columns already exist from the
-- CREATE.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pressure_median   DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pressure_p95      DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pressure_p995     DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ipap_median       DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ipap_p95          DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ipap_p995         DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS epap_median       DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS epap_p95          DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS epap_p995         DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS flow_limit_median DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS flow_limit_p95    DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS flow_limit_p995   DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS leak_median       DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS leak_p95          DOUBLE DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS leak_p995         DOUBLE DEFAULT NULL;

-- `excluded_sessions` is the operator-facing "don't count this session
-- in the night's stats" list. Inserts and deletes are both used —
-- toggle = insert if absent, delete if present. recompute_summary
-- consumes this list. Persists across re-imports; orphan rows (where
-- a previously-non-empty session becomes empty after re-import) are
-- accepted as a low-frequency edge case until Phase 4.5 housekeeping.
CREATE TABLE IF NOT EXISTS excluded_sessions (
    date DATE NOT NULL,
    session_id INTEGER NOT NULL,
    excluded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, session_id)
);

-- Phase 4 Ticket 2 (v5, 2026-05-14) — async import queue.
--
-- Job-state backing store for the in-process import worker. Each
-- import — whether triggered via POST /imports (path-based) or POST
-- /imports/upload (folder upload) — lands here in status 'queued'.
-- The worker (an asyncio task started at app lifespan) picks the
-- oldest queued job, flips status to 'running', invokes
-- import_path(), then writes status to 'completed' or 'failed' with
-- the ImportLogEntry serialized into result_json (or error_message
-- on failure).
--
-- Why DuckDB-backed rather than in-memory: API restarts shouldn't
-- lose in-flight job state. A 'running' row left after restart
-- gets surfaced to the operator as 'orphaned' so they can decide
-- whether to retry. Re-running the v5 migration is idempotent.
--
-- One worker, one job at a time. No concurrent imports needed for
-- a single operator, and parallel writes against a single DuckDB
-- file would serialize through the writer lock regardless.
CREATE SEQUENCE IF NOT EXISTS import_jobs_id_seq START 1;
CREATE TABLE IF NOT EXISTS import_jobs (
    id BIGINT PRIMARY KEY,
    status VARCHAR NOT NULL,        -- queued | running | completed | failed
    source_path VARCHAR,            -- for path-based imports
    upload_dir VARCHAR,             -- for folder-upload imports (tempdir path)
    force_reimport BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    result_json JSON,               -- serialized ImportLogEntry on success
    error_message VARCHAR
);
ALTER TABLE import_jobs ALTER COLUMN id SET DEFAULT nextval('import_jobs_id_seq');

-- Sequences for surrogate IDs are now declared inline above each table
-- they belong to (Phase 3 Item 1A), so that DEFAULT nextval() can
-- reference them in the same logical block. The redundant declarations
-- previously here are removed; CREATE SEQUENCE IF NOT EXISTS is
-- idempotent and the inline forms above carry the canonical definition.
