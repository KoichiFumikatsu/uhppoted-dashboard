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


# añadir a doors_analytics/db.py
def upsert_events(conn, events):
    now = datetime.utcnow().isoformat(timespec="seconds")
    rows = [(
        e["device_id"], e["idx"], e.get("timestamp"), e.get("card"),
        e.get("door"), e.get("door_name"), e.get("granted"), e.get("reason"),
        e.get("direction"), e.get("event_type"), e.get("sede"),
        e.get("source"), now,
    ) for e in events]
    conn.executemany(
        "INSERT OR IGNORE INTO events "
        "(device_id, idx, timestamp, card, door, door_name, granted, reason, "
        " direction, event_type, sede, source, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)
