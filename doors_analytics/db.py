# doors_analytics/db.py
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    device_id   INTEGER NOT NULL,
    idx         INTEGER NOT NULL,
    timestamp   TEXT,
    card        INTEGER,
    door        INTEGER,
    door_name   TEXT,
    granted     INTEGER,
    reason      INTEGER,
    direction   INTEGER,
    event_type  INTEGER,
    sede        TEXT,
    source      TEXT,
    ingested_at TEXT,
    PRIMARY KEY (device_id, idx)
);
CREATE INDEX IF NOT EXISTS ix_events_ts   ON events(timestamp);
CREATE INDEX IF NOT EXISTS ix_events_card ON events(card);
CREATE INDEX IF NOT EXISTS ix_events_sede ON events(sede);

CREATE TABLE IF NOT EXISTS controllers (
    serial       INTEGER PRIMARY KEY,
    name         TEXT,
    sede         TEXT,
    ip           TEXT,
    listener     TEXT,
    network_json TEXT,
    added        TEXT
);

CREATE TABLE IF NOT EXISTS doors_meta (
    device_id       INTEGER NOT NULL,
    door            INTEGER NOT NULL,
    label           TEXT,
    entry_exit_role TEXT,
    PRIMARY KEY (device_id, door)
);

CREATE TABLE IF NOT EXISTS card_persons (
    card    INTEGER PRIMARY KEY,
    name    TEXT,
    sede    TEXT,
    rol     TEXT,
    updated TEXT
);

CREATE TABLE IF NOT EXISTS ingest_state (
    source     TEXT NOT NULL,
    device_id  INTEGER NOT NULL,
    last_index INTEGER NOT NULL DEFAULT 0,
    last_run   TEXT,
    PRIMARY KEY (source, device_id)
);
"""


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()
