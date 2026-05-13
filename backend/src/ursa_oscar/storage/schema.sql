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

CREATE TABLE IF NOT EXISTS nightly_events (
    id BIGINT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS manual_logs (
    id BIGINT PRIMARY KEY,
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
CREATE INDEX IF NOT EXISTS idx_manual_logs_date ON manual_logs(date);
CREATE INDEX IF NOT EXISTS idx_manual_logs_type ON manual_logs(log_type);
CREATE INDEX IF NOT EXISTS idx_manual_logs_category ON manual_logs(category);

CREATE TABLE IF NOT EXISTS config (
    key VARCHAR PRIMARY KEY,
    value VARCHAR,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS import_log (
    id BIGINT PRIMARY KEY,
    import_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_path VARCHAR,
    nights_imported INTEGER,
    earliest_date DATE,
    latest_date DATE,
    status VARCHAR,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description VARCHAR
);

-- Sequences for surrogate IDs (DuckDB does not auto-increment by default)
CREATE SEQUENCE IF NOT EXISTS nightly_events_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS manual_logs_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS import_log_id_seq START 1;
