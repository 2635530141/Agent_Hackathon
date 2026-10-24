-- M1 local SQLite schema, applied explicitly by SQLiteScheduleRepository.migrate().
-- No identity, courses, request, acceptance or production sessions tables.
CREATE TABLE IF NOT EXISTS smcal_schema_version (version INTEGER NOT NULL PRIMARY KEY);
CREATE TABLE IF NOT EXISTS smcal_seed_markers (key VARCHAR(128) NOT NULL PRIMARY KEY);
CREATE TABLE IF NOT EXISTS smcal_schedule_state (
    owner_id VARCHAR(128) NOT NULL PRIMARY KEY,
    schedule_version INTEGER NOT NULL,
    snapshot_version INTEGER NOT NULL,
    refresh_generation INTEGER NOT NULL,
    connection_status VARCHAR(24) NOT NULL,
    last_success_at DATETIME,
    coverage_start DATETIME,
    coverage_end DATETIME
);
CREATE TABLE IF NOT EXISTS smcal_records (
    id VARCHAR(128) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(128) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    payload JSON NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_smcal_records_owner_id ON smcal_records (owner_id);
CREATE TABLE IF NOT EXISTS smcal_busy_intervals (
    owner_id VARCHAR(128) NOT NULL,
    id VARCHAR(128) NOT NULL,
    start DATETIME NOT NULL,
    end DATETIME NOT NULL,
    PRIMARY KEY (owner_id, id)
);
CREATE TABLE IF NOT EXISTS smcal_private_events (
    owner_id VARCHAR(128) NOT NULL,
    id VARCHAR(128) NOT NULL,
    start DATETIME NOT NULL,
    end DATETIME NOT NULL,
    private_title VARCHAR(500) NOT NULL,
    PRIMARY KEY (owner_id, id)
);
