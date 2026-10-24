-- Explicit Calendar-only migration. No Session/Invitation tables.
CREATE TABLE IF NOT EXISTS smcal_exports (
 logical_key VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(128) NOT NULL,
 session_id VARCHAR(128) NOT NULL, calendar_id VARCHAR(16) NOT NULL,
 event_id VARCHAR(128) NOT NULL UNIQUE, marker VARCHAR(64) NOT NULL,
 fingerprint VARCHAR(64), session_revision VARCHAR(128), account_subject VARCHAR(255), generation INTEGER,
 payload JSON, status VARCHAR(24) NOT NULL, intent VARCHAR(8), last_error VARCHAR(80), html_link TEXT,
 lease_owner VARCHAR(64), lease_until DATETIME, exported_at DATETIME, removed_at DATETIME, updated_at DATETIME NOT NULL,
 UNIQUE (session_id, owner_id, calendar_id)
);
CREATE TABLE IF NOT EXISTS smcal_export_approvals (
 approval_hash VARCHAR(64) PRIMARY KEY, logical_key VARCHAR(64) NOT NULL,
 owner_id VARCHAR(128) NOT NULL, fingerprint VARCHAR(64) NOT NULL,
 generation INTEGER NOT NULL, kind VARCHAR(8) NOT NULL, expires_at DATETIME NOT NULL
);
