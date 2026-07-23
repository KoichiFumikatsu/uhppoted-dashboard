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

CREATE TABLE IF NOT EXISTS portal_audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    actor   TEXT,
    action  TEXT,
    target  TEXT,
    details TEXT,
    result  TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON portal_audit(ts);

CREATE TABLE IF NOT EXISTS card_door_overrides (
    card     INTEGER NOT NULL,
    door_oid TEXT    NOT NULL,
    value    TEXT    NOT NULL,
    updated  TEXT,
    PRIMARY KEY (card, door_oid)
);
CREATE INDEX IF NOT EXISTS ix_cdo_door ON card_door_overrides(door_oid);

-- Modelo de acceso (hoy propiedad del panel httpd: cards.json/groups.json). Espejo
-- en la DB para que el portal sea su dueno y el panel se pueda apagar.
CREATE TABLE IF NOT EXISTS model_cards (
    card       INTEGER PRIMARY KEY,
    name       TEXT,
    valid_from TEXT,
    valid_to   TEXT,
    groups     TEXT,          -- JSON array de OIDs de grupo
    oid        TEXT,
    updated    TEXT
);
CREATE TABLE IF NOT EXISTS model_groups (
    oid     TEXT PRIMARY KEY,
    name    TEXT,
    doors   TEXT,             -- JSON array de OIDs de puerta
    updated TEXT
);

CREATE TABLE IF NOT EXISTS time_profiles (
    id       INTEGER PRIMARY KEY,   -- 2..254; MISMO significado en las 5 placas
    from_d   TEXT,
    to_d     TEXT,
    weekdays TEXT,                  -- CSV Mon,Tue,Wed,Thu,Fri,Sat,Sun
    segments TEXT,                  -- JSON [[start,end],...]
    linked   INTEGER DEFAULT 0,
    updated  TEXT
);
"""

# doors_meta nacio sin estas columnas: se agregan en caliente porque la tabla ya
# existe en las bases desplegadas y CREATE TABLE IF NOT EXISTS no las agregaria.
_ALTERS = [
    ("doors_meta", "used", "INTEGER NOT NULL DEFAULT 1"),
    ("doors_meta", "updated", "TEXT"),
]


def init_db(conn):
    conn.executescript(SCHEMA)
    for table, column, decl in _ALTERS:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in cols:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    conn.commit()


def insert_audit(conn, actor, action, target, details, result):
    """Append one portal-action audit row. Best-effort; caller swallows errors."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO portal_audit (ts, actor, action, target, details, result) "
        "VALUES (?,?,?,?,?,?)",
        (now, actor, action, target, details, result))
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


def _now():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ---- overrides de puerta por tarjeta ----

def card_overrides(conn, card=None):
    """{card_str: {door_oid: value}}; con `card` devuelve solo esa tarjeta."""
    if card is None:
        rows = conn.execute("SELECT card, door_oid, value FROM card_door_overrides")
    else:
        rows = conn.execute(
            "SELECT card, door_oid, value FROM card_door_overrides WHERE card=?",
            (int(card),))
    out = {}
    for c, oid, v in rows:
        out.setdefault(str(c), {})[oid] = v
    return out


def card_override_ts(conn, card):
    r = conn.execute("SELECT MAX(updated) FROM card_door_overrides WHERE card=?",
                     (int(card),)).fetchone()
    return r[0] if r and r[0] else None


def set_card_overrides(conn, card, doors):
    """Reemplaza los overrides de la tarjeta en una transaccion (doors={oid: value})."""
    with conn:
        conn.execute("DELETE FROM card_door_overrides WHERE card=?", (int(card),))
        if doors:
            conn.executemany(
                "INSERT INTO card_door_overrides (card, door_oid, value, updated) "
                "VALUES (?,?,?,?)",
                [(int(card), str(o), str(v), _now()) for o, v in doors.items()])


def clear_card_overrides(conn, card):
    with conn:
        conn.execute("DELETE FROM card_door_overrides WHERE card=?", (int(card),))


# ---- metadata de puerta (nombre propio del portal + marca de uso) ----

def doors_meta(conn):
    """{(device_id, door): {'label':..., 'used':bool}}"""
    out = {}
    for dev, dr, label, used in conn.execute(
            "SELECT device_id, door, label, used FROM doors_meta"):
        out[(int(dev), int(dr))] = {'label': label, 'used': bool(used)}
    return out


def set_door_meta(conn, device_id, door, label=None, used=None):
    with conn:
        conn.execute(
            "INSERT INTO doors_meta (device_id, door, label, used, updated) "
            "VALUES (?,?,?,COALESCE(?,1),?) "
            "ON CONFLICT(device_id, door) DO UPDATE SET "
            "label=COALESCE(excluded.label, doors_meta.label), "
            "used=COALESCE(?, doors_meta.used), updated=excluded.updated",
            (int(device_id), int(door), label, used, _now(), used))


# ---- nombre propio del portal para controladores ----

def controller_names(conn):
    return {str(s): n for s, n in
            conn.execute("SELECT serial, name FROM controllers WHERE name IS NOT NULL")}


def set_controller_name(conn, serial, name):
    with conn:
        conn.execute(
            "INSERT INTO controllers (serial, name, added) VALUES (?,?,?) "
            "ON CONFLICT(serial) DO UPDATE SET name=excluded.name",
            (int(serial), name, _now()))


# ---- modelo de acceso: cards y groups (espejo/futuro dueno del panel) ----
import json as _json


def model_cards(conn):
    """Lista con la misma forma que cards.json: {OID, card, name, from, to, groups[]}."""
    out = []
    for card, name, vf, vt, groups, oid in conn.execute(
            "SELECT card, name, valid_from, valid_to, groups, oid FROM model_cards ORDER BY card"):
        out.append({'OID': oid, 'card': card, 'name': name or '',
                    'from': vf or '', 'to': vt or '',
                    'groups': _json.loads(groups or '[]')})
    return out


def model_groups(conn):
    """Lista con la forma de groups.json: {OID, name, doors[]}."""
    out = []
    for oid, name, doors in conn.execute(
            "SELECT oid, name, doors FROM model_groups ORDER BY oid"):
        out.append({'OID': oid, 'name': name or '', 'doors': _json.loads(doors or '[]')})
    return out


def replace_model_cards(conn, cards):
    """Reemplaza TODO el modelo de tarjetas en una transaccion (espejo del panel).
    cards: iterable de dicts forma cards.json."""
    rows = []
    for c in cards:
        gids = c.get('groups') or []
        if isinstance(gids, dict):
            gids = [k for k, v in gids.items() if v]
        rows.append((int(c['card']), c.get('name', ''), c.get('from', ''), c.get('to', ''),
                     _json.dumps(list(gids)), c.get('OID', ''), _now()))
    with conn:
        conn.execute("DELETE FROM model_cards")
        conn.executemany(
            "INSERT INTO model_cards (card, name, valid_from, valid_to, groups, oid, updated) "
            "VALUES (?,?,?,?,?,?,?)", rows)


def replace_model_groups(conn, groups):
    rows = []
    for g in groups:
        doors = g.get('doors') or []
        if isinstance(doors, dict):
            doors = [k for k, v in doors.items() if v]
        rows.append((g.get('OID', ''), g.get('name', ''), _json.dumps(list(doors)), _now()))
    with conn:
        conn.execute("DELETE FROM model_groups")
        conn.executemany(
            "INSERT INTO model_groups (oid, name, doors, updated) VALUES (?,?,?,?)", rows)


# ---- horarios (time profiles) — definicion central, misma para las 5 placas ----


def _profile_row(r):
    return {'id': r[0], 'from': r[1], 'to': r[2],
            'weekdays': [w for w in (r[3] or '').split(',') if w],
            'segments': _json.loads(r[4] or '[]'), 'linked': r[5] or 0}


def time_profiles(conn):
    return [_profile_row(r) for r in conn.execute(
        "SELECT id, from_d, to_d, weekdays, segments, linked "
        "FROM time_profiles ORDER BY id")]


def time_profile(conn, pid):
    r = conn.execute(
        "SELECT id, from_d, to_d, weekdays, segments, linked "
        "FROM time_profiles WHERE id=?", (int(pid),)).fetchone()
    return _profile_row(r) if r else None


def set_time_profile(conn, pid, from_d, to_d, weekdays, segments, linked=0):
    with conn:
        conn.execute(
            "INSERT INTO time_profiles (id, from_d, to_d, weekdays, segments, linked, updated) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET from_d=excluded.from_d, to_d=excluded.to_d, "
            "weekdays=excluded.weekdays, segments=excluded.segments, "
            "linked=excluded.linked, updated=excluded.updated",
            (int(pid), from_d, to_d, ','.join(weekdays),
             _json.dumps([list(s) for s in segments]), int(linked or 0), _now()))


def delete_time_profile(conn, pid):
    with conn:
        conn.execute("DELETE FROM time_profiles WHERE id=?", (int(pid),))


# añadir a doors_analytics/db.py
def get_cursor(conn, source, device_id):
    r = conn.execute(
        "SELECT last_index FROM ingest_state WHERE source=? AND device_id=?",
        (source, device_id)).fetchone()
    return r[0] if r else 0


def set_cursor(conn, source, device_id, last_index):
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO ingest_state (source, device_id, last_index, last_run) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(source, device_id) DO UPDATE SET "
        "last_index=excluded.last_index, last_run=excluded.last_run",
        (source, device_id, last_index, now))
    conn.commit()
