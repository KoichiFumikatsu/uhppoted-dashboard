# Datastore + Ingesta (uhppoted dashboard) — Plan 1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Montar el datastore SQLite y el daemon de ingesta que puebla `events` desde Palmetto (`events.json`) y Tequendama (`teq-events.json`), idempotente por `(device_id, idx)`, con backfill desde 2026-01-01.

**Architecture:** Paquete Python stdlib `doors_analytics` en `/root/uhppoted-dashboard/` (repo git en el server). Colectores puros que leen los JSON existentes → funciones de upsert idempotente sobre SQLite → orquestador `run_once` disparado por un `systemd .timer` cada 10 min. Sin HTTP en el daemon (la API es Plan 2). Todo el trabajo se ejecuta **en el server `192.168.12.25` vía SSH**.

**Tech Stack:** Python 3.8 stdlib únicamente (`sqlite3`, `json`, `datetime`, `unittest`), systemd. **No hay pip/pytest en el server** → tests con `python3 -m unittest`.

## Global Constraints

- **Solo stdlib de Python 3.8.** Prohibido `pip install` / dependencias externas (el server no tiene pip y apt está roto). Tests con `unittest`, no pytest.
- **Ejecución en el server:** `ssh root@192.168.12.25`, todo dentro de `/root/uhppoted-dashboard/`. El daemon corre como **root** (los servicios uhppoted corren como root).
- **DB:** `/var/uhppoted/analytics/doors.db` (SQLite stdlib 3.31).
- **Idempotencia:** clave primaria `events(device_id, idx)`. Re-ejecutar la ingesta nunca duplica.
- **Backfill:** solo eventos con `timestamp[:10] >= "2026-01-01"`.
- **Serials:** Palmetto `222451671`; Teq `225088590, 425036574, 423150802, 223205300`.
- **Fuentes de eventos (existentes, no se tocan):** `/var/uhppoted/httpd/system/events.json` (Palmetto, trae `direction`), `/var/uhppoted/teq-events.json` (mantenido por `teq-events-poller.service`, sin `direction`).
- **Contrato de evento normalizado** (dict que producen los colectores y consume `upsert_events`):
  `{device_id:int, idx:int, timestamp:"YYYY-MM-DD HH:MM:SS", card:int, door:int, door_name:str, granted:0|1, reason:int, direction:int|None, event_type:int|None, sede:str, source:"palmetto"|"teq"}`

---

## File Structure

```
/root/uhppoted-dashboard/            (repo git)
  doors_analytics/
    __init__.py
    config.py            # paths, serials, since_date
    db.py                # init_db, upsert_events, get_cursor, set_cursor
    collectors.py        # collect_palmetto, collect_teq
    ingest.py            # run_once (orquestador)
  bin/
    doors-ingest         # entrypoint para systemd (ejecutable)
  tests/
    test_db.py
    test_collectors.py
    test_ingest.py
  README.md
  .gitignore
/etc/systemd/system/doors-ingest.service
/etc/systemd/system/doors-ingest.timer
```

**Decisión documentada (alcance Teq en Plan 1):** la ingesta de Teq lee `teq-events.json` (ventana rolling ~8 días que mantiene el poller). Cubre "de este año en adelante" para Palmetto (histórico completo en `events.json`) y "reciente + hacia adelante" para Teq. La **cosecha profunda de los buffers de las placas Teq (hasta ene-2026)** queda **fuera de Plan 1** — la habilita el edge box Linux (2 días) o una tarea CLI dedicada posterior; el esquema y los cursores ya la soportan sin rework.

---

### Task 1: Scaffold del proyecto + git + runner de tests

**Files:**
- Create: `/root/uhppoted-dashboard/doors_analytics/__init__.py`
- Create: `/root/uhppoted-dashboard/tests/__init__.py` (vacío)
- Create: `/root/uhppoted-dashboard/.gitignore`
- Create: `/root/uhppoted-dashboard/README.md`

**Interfaces:**
- Produces: estructura de paquete importable como `doors_analytics` desde la raíz del repo.

- [ ] **Step 1: Crear estructura y repo git (en el server)**

```bash
ssh root@192.168.12.25
mkdir -p /root/uhppoted-dashboard/doors_analytics /root/uhppoted-dashboard/tests /root/uhppoted-dashboard/bin
cd /root/uhppoted-dashboard
touch doors_analytics/__init__.py tests/__init__.py
printf '__pycache__/\n*.pyc\n*.db\n' > .gitignore
printf '# uhppoted-dashboard\n\nDatastore + ingesta + API + UI para gestión de controladores, histórico de eventos y KPIs de acceso.\nSolo stdlib de Python 3.8. Tests: `python3 -m unittest discover -s tests -v`.\n' > README.md
git init -q && git add -A && git commit -q -m "chore: scaffold doors_analytics package"
```

- [ ] **Step 2: Verificar que unittest corre (sin tests aún)**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest discover -s tests -v`
Expected: `Ran 0 tests` sin errores de import.

---

### Task 2: Esquema de la DB (`db.init_db`)

**Files:**
- Create: `/root/uhppoted-dashboard/doors_analytics/db.py`
- Test: `/root/uhppoted-dashboard/tests/test_db.py`

**Interfaces:**
- Produces: `init_db(conn)` — crea todas las tablas, idempotente (IF NOT EXISTS).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_db.py
import sqlite3
import unittest
from doors_analytics import db


class TestInitDb(unittest.TestCase):
    def test_creates_tables_idempotent(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        db.init_db(conn)  # segunda vez no debe fallar
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(
            {"events", "controllers", "doors_meta", "card_persons",
             "ingest_state"}.issubset(names))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_db -v`
Expected: FAIL con `ModuleNotFoundError` o `AttributeError: module 'doors_analytics.db' has no attribute 'init_db'`.

- [ ] **Step 3: Implementar el esquema**

```python
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
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -q -m "feat: db schema (init_db)"
```

---

### Task 3: Upsert idempotente de eventos (`db.upsert_events`)

**Files:**
- Modify: `/root/uhppoted-dashboard/doors_analytics/db.py`
- Test: `/root/uhppoted-dashboard/tests/test_db.py`

**Interfaces:**
- Consumes: `init_db(conn)`; dicts con el contrato de evento normalizado.
- Produces: `upsert_events(conn, events) -> int` (nº de filas ofrecidas). Inserta con `INSERT OR IGNORE` sobre PK `(device_id, idx)`; agrega `ingested_at`.

- [ ] **Step 1: Escribir el test que falla**

```python
# añadir a tests/test_db.py
class TestUpsertEvents(unittest.TestCase):
    def _ev(self, idx):
        return {"device_id": 222451671, "idx": idx,
                "timestamp": "2026-03-01 08:00:00", "card": 17059974,
                "door": 1, "door_name": "S1 Porteria", "granted": 1,
                "reason": 1, "direction": 1, "event_type": 1,
                "sede": "Palmetto", "source": "palmetto"}

    def test_upsert_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        batch = [self._ev(1), self._ev(2)]
        db.upsert_events(conn, batch)
        db.upsert_events(conn, batch)  # segunda vez: sin duplicados
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 2)

    def test_upsert_preserves_fields(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        db.upsert_events(conn, [self._ev(5)])
        row = conn.execute(
            "SELECT card, direction, granted, sede FROM events WHERE idx=5"
        ).fetchone()
        self.assertEqual(row, (17059974, 1, 1, "Palmetto"))
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_db -v`
Expected: FAIL con `AttributeError: ... has no attribute 'upsert_events'`.

- [ ] **Step 3: Implementar el upsert**

```python
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
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_db -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -q -m "feat: idempotent upsert_events"
```

---

### Task 4: Cursores de ingesta (`db.get_cursor` / `db.set_cursor`)

**Files:**
- Modify: `/root/uhppoted-dashboard/doors_analytics/db.py`
- Test: `/root/uhppoted-dashboard/tests/test_db.py`

**Interfaces:**
- Consumes: `init_db(conn)`.
- Produces: `get_cursor(conn, source, device_id) -> int` (default 0); `set_cursor(conn, source, device_id, last_index)` (upsert por `(source, device_id)`).

- [ ] **Step 1: Escribir el test que falla**

```python
# añadir a tests/test_db.py
class TestCursors(unittest.TestCase):
    def test_default_zero_then_set_get(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        self.assertEqual(db.get_cursor(conn, "palmetto", 222451671), 0)
        db.set_cursor(conn, "palmetto", 222451671, 42)
        self.assertEqual(db.get_cursor(conn, "palmetto", 222451671), 42)
        db.set_cursor(conn, "palmetto", 222451671, 99)  # actualiza
        self.assertEqual(db.get_cursor(conn, "palmetto", 222451671), 99)
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_db -v`
Expected: FAIL con `AttributeError: ... has no attribute 'get_cursor'`.

- [ ] **Step 3: Implementar los cursores**

```python
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
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_db -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -q -m "feat: ingest cursors get/set"
```

---

### Task 5: Colectores (`collectors.collect_palmetto` / `collect_teq`)

**Files:**
- Create: `/root/uhppoted-dashboard/doors_analytics/collectors.py`
- Test: `/root/uhppoted-dashboard/tests/test_collectors.py`

**Interfaces:**
- Produces:
  - `collect_palmetto(events_json_path, serial, since_date, after_index) -> list[dict]` — lee `events.json`, filtra `device-id == serial`, `timestamp[:10] >= since_date`, `index > after_index`; normaliza (preserva `direction`, `event-type`); `sede="Palmetto"`, `source="palmetto"`; ordenado por `idx`.
  - `collect_teq(teq_json_path, after_index_by_serial, since_date) -> list[dict]` — lee `teq-events.json`; por evento usa `after_index_by_serial.get(device-id, 0)`; filtra `index > cursor` y `timestamp[:10] >= since_date`; `direction=None`, `event_type=None`, `sede="Tequendama"`, `source="teq"`.
- Ambos consumidos por `db.upsert_events` (contrato de evento normalizado).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_collectors.py
import json
import os
import tempfile
import unittest
from doors_analytics import collectors


def _write(tmp, obj):
    path = os.path.join(tmp, "e.json")
    with open(path, "w") as f:
        json.dump(obj, f)
    return path


class TestCollectPalmetto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = {"events": [
            {"device-id": 222451671, "index": 10, "timestamp": "2025-12-31 09:00:00 -05",
             "event-type": 1, "door": 1, "direction": 1, "card": 111,
             "granted": True, "reason": 1, "door-name": "S1 Porteria"},   # antes de 2026 -> fuera
            {"device-id": 222451671, "index": 11, "timestamp": "2026-01-02 07:15:00 -05",
             "event-type": 1, "door": 1, "direction": 1, "card": 222,
             "granted": True, "reason": 1, "door-name": "S1 Porteria"},   # ok
            {"device-id": 222451671, "index": 12, "timestamp": "2026-01-03 18:40:00 -05",
             "event-type": 1, "door": 1, "direction": 2, "card": 222,
             "granted": False, "reason": 6, "door-name": "S1 Porteria"},  # ok, granted False
            {"device-id": 423150802, "index": 99, "timestamp": "2026-02-01 10:00:00 -05",
             "event-type": 1, "door": 1, "direction": 1, "card": 333,
             "granted": True, "reason": 1, "door-name": "Teq .150 P1"},   # otro device -> fuera
        ]}
        self.path = _write(self.tmp, self.data)

    def test_filters_by_date_device_and_cursor(self):
        out = collectors.collect_palmetto(self.path, 222451671, "2026-01-01", after_index=0)
        idxs = [e["idx"] for e in out]
        self.assertEqual(idxs, [11, 12])  # 10 (2025) y 99 (otro device) fuera

    def test_cursor_excludes_seen(self):
        out = collectors.collect_palmetto(self.path, 222451671, "2026-01-01", after_index=11)
        self.assertEqual([e["idx"] for e in out], [12])

    def test_normalization(self):
        out = collectors.collect_palmetto(self.path, 222451671, "2026-01-01", after_index=0)
        e = out[0]
        self.assertEqual(e["timestamp"], "2026-01-02 07:15:00")  # tz recortado
        self.assertEqual(e["granted"], 1)
        self.assertEqual(e["direction"], 1)
        self.assertEqual(e["sede"], "Palmetto")
        self.assertEqual(e["source"], "palmetto")
        self.assertEqual(out[1]["granted"], 0)  # False -> 0


class TestCollectTeq(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = {"events": [
            {"device-id": 225088590, "index": 500, "timestamp": "2026-07-14 15:48:53",
             "card": 17059974, "door": 1, "door-name": "Teq .125 P1",
             "granted": True, "reason": 1},
            {"device-id": 425036574, "index": 800, "timestamp": "2026-07-14 16:00:00",
             "card": 999, "door": 1, "door-name": "Teq .12 P1",
             "granted": True, "reason": 1},
        ], "cursor": {}}
        self.path = _write(self.tmp, self.data)

    def test_per_serial_cursor_and_defaults(self):
        after = {225088590: 500}  # ya vimos hasta 500 -> excluye ese; .12 sin cursor -> incluye
        out = collectors.collect_teq(self.path, after, "2026-01-01")
        self.assertEqual([(e["device_id"], e["idx"]) for e in out], [(425036574, 800)])
        e = out[0]
        self.assertIsNone(e["direction"])
        self.assertEqual(e["sede"], "Tequendama")
        self.assertEqual(e["source"], "teq")
        self.assertEqual(e["granted"], 1)
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_collectors -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'doors_analytics.collectors'`.

- [ ] **Step 3: Implementar los colectores**

```python
# doors_analytics/collectors.py
import json


def _load(path):
    with open(path) as f:
        d = json.load(f)
    return d.get("events", d if isinstance(d, list) else [])


def collect_palmetto(events_json_path, serial, since_date, after_index):
    out = []
    for e in _load(events_json_path):
        if e.get("device-id") != serial:
            continue
        ts = (e.get("timestamp") or "")
        if ts[:10] < since_date:
            continue
        idx = e.get("index")
        if idx is None or idx <= after_index:
            continue
        out.append({
            "device_id": serial,
            "idx": idx,
            "timestamp": ts[:19],
            "card": e.get("card"),
            "door": e.get("door"),
            "door_name": e.get("door-name"),
            "granted": 1 if e.get("granted") else 0,
            "reason": e.get("reason"),
            "direction": e.get("direction"),
            "event_type": e.get("event-type"),
            "sede": "Palmetto",
            "source": "palmetto",
        })
    out.sort(key=lambda x: x["idx"])
    return out


def collect_teq(teq_json_path, after_index_by_serial, since_date):
    out = []
    for e in _load(teq_json_path):
        serial = e.get("device-id")
        cursor = after_index_by_serial.get(serial, 0)
        ts = (e.get("timestamp") or "")
        idx = e.get("index")
        if idx is None or idx <= cursor:
            continue
        if ts[:10] < since_date:
            continue
        out.append({
            "device_id": serial,
            "idx": idx,
            "timestamp": ts[:19],
            "card": e.get("card"),
            "door": e.get("door"),
            "door_name": e.get("door-name"),
            "granted": 1 if e.get("granted") else 0,
            "reason": e.get("reason"),
            "direction": None,
            "event_type": None,
            "sede": "Tequendama",
            "source": "teq",
        })
    out.sort(key=lambda x: (x["device_id"], x["idx"]))
    return out
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_collectors -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -q -m "feat: palmetto + teq collectors"
```

---

### Task 6: Orquestador (`ingest.run_once`) + config

**Files:**
- Create: `/root/uhppoted-dashboard/doors_analytics/config.py`
- Create: `/root/uhppoted-dashboard/doors_analytics/ingest.py`
- Test: `/root/uhppoted-dashboard/tests/test_ingest.py`

**Interfaces:**
- Consumes: `db.get_cursor/set_cursor/upsert_events`, `collectors.collect_palmetto/collect_teq`.
- Produces: `run_once(conn, events_json, teq_json, palmetto_serial, teq_serials, since_date) -> dict` con `{"palmetto": n, "teq": m}`; avanza cursores por fuente/serial; idempotente.
- `config.py` expone: `DB_PATH, EVENTS_JSON, TEQ_JSON, SINCE_DATE, PALMETTO_SERIAL, TEQ_SERIALS`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_ingest.py
import json
import os
import sqlite3
import tempfile
import unittest
from doors_analytics import db, ingest


class TestRunOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pal = os.path.join(self.tmp, "palmetto.json")
        self.teq = os.path.join(self.tmp, "teq.json")
        with open(self.pal, "w") as f:
            json.dump({"events": [
                {"device-id": 222451671, "index": 11, "timestamp": "2026-01-02 07:15:00 -05",
                 "event-type": 1, "door": 1, "direction": 1, "card": 222,
                 "granted": True, "reason": 1, "door-name": "S1 Porteria"},
                {"device-id": 222451671, "index": 12, "timestamp": "2026-01-03 18:40:00 -05",
                 "event-type": 1, "door": 1, "direction": 2, "card": 222,
                 "granted": True, "reason": 1, "door-name": "S1 Porteria"},
            ]}, f)
        with open(self.teq, "w") as f:
            json.dump({"events": [
                {"device-id": 225088590, "index": 500, "timestamp": "2026-07-14 15:48:53",
                 "card": 17059974, "door": 1, "door-name": "Teq .125 P1",
                 "granted": True, "reason": 1},
            ]}, f)
        self.conn = sqlite3.connect(":memory:")
        db.init_db(self.conn)

    def _run(self):
        return ingest.run_once(self.conn, self.pal, self.teq,
                               222451671, [225088590, 425036574], "2026-01-01")

    def test_ingests_and_counts(self):
        counts = self._run()
        self.assertEqual(counts, {"palmetto": 2, "teq": 1})
        total = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(total, 3)

    def test_second_run_is_idempotent(self):
        self._run()
        counts2 = self._run()  # cursores ya avanzados -> nada nuevo
        self.assertEqual(counts2, {"palmetto": 0, "teq": 0})
        total = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(total, 3)

    def test_cursors_advanced(self):
        self._run()
        self.assertEqual(db.get_cursor(self.conn, "palmetto", 222451671), 12)
        self.assertEqual(db.get_cursor(self.conn, "teq", 225088590), 500)
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_ingest -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'doors_analytics.ingest'`.

- [ ] **Step 3: Implementar config y orquestador**

```python
# doors_analytics/config.py
DB_PATH = "/var/uhppoted/analytics/doors.db"
EVENTS_JSON = "/var/uhppoted/httpd/system/events.json"
TEQ_JSON = "/var/uhppoted/teq-events.json"
SINCE_DATE = "2026-01-01"
PALMETTO_SERIAL = 222451671
TEQ_SERIALS = [225088590, 425036574, 423150802, 223205300]
```

```python
# doors_analytics/ingest.py
from doors_analytics import db, collectors


def run_once(conn, events_json, teq_json, palmetto_serial, teq_serials, since_date):
    counts = {}

    # --- Palmetto ---
    cur = db.get_cursor(conn, "palmetto", palmetto_serial)
    pev = collectors.collect_palmetto(events_json, palmetto_serial, since_date, cur)
    db.upsert_events(conn, pev)
    if pev:
        db.set_cursor(conn, "palmetto", palmetto_serial,
                      max(e["idx"] for e in pev))
    counts["palmetto"] = len(pev)

    # --- Tequendama ---
    after = {s: db.get_cursor(conn, "teq", s) for s in teq_serials}
    tev = collectors.collect_teq(teq_json, after, since_date)
    db.upsert_events(conn, tev)
    maxes = {}
    for e in tev:
        maxes[e["device_id"]] = max(maxes.get(e["device_id"], 0), e["idx"])
    for serial, mx in maxes.items():
        db.set_cursor(conn, "teq", serial, mx)
    counts["teq"] = len(tev)

    return counts
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_ingest -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -q -m "feat: run_once orchestrator + config"
```

---

### Task 7: Entrypoint + systemd (service + timer)

**Files:**
- Create: `/root/uhppoted-dashboard/bin/doors-ingest` (ejecutable)
- Create: `/etc/systemd/system/doors-ingest.service`
- Create: `/etc/systemd/system/doors-ingest.timer`

**Interfaces:**
- Consumes: `config`, `db.init_db`, `ingest.run_once`.
- Produces: comando `doors-ingest` que abre `config.DB_PATH`, inicializa y corre `run_once`, imprime los counts. Timer que lo dispara cada 10 min.

- [ ] **Step 1: Crear el entrypoint**

```python
# bin/doors-ingest
#!/usr/bin/env python3
import os
import sqlite3
import sys

sys.path.insert(0, "/root/uhppoted-dashboard")
from doors_analytics import config, db, ingest

os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
conn = sqlite3.connect(config.DB_PATH)
db.init_db(conn)
counts = ingest.run_once(
    conn, config.EVENTS_JSON, config.TEQ_JSON,
    config.PALMETTO_SERIAL, config.TEQ_SERIALS, config.SINCE_DATE)
conn.close()
print("doors-ingest:", counts)
```

```bash
chmod +x /root/uhppoted-dashboard/bin/doors-ingest
```

- [ ] **Step 2: Crear las units systemd**

```ini
# /etc/systemd/system/doors-ingest.service
[Unit]
Description=uhppoted dashboard - ingesta de eventos a SQLite
After=network.target

[Service]
Type=oneshot
ExecStart=/root/uhppoted-dashboard/bin/doors-ingest
```

```ini
# /etc/systemd/system/doors-ingest.timer
[Unit]
Description=Dispara doors-ingest cada 10 minutos

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Recargar systemd y correr el service una vez (backfill inicial)**

Run:
```bash
systemctl daemon-reload
systemctl start doors-ingest.service
journalctl -u doors-ingest.service --no-pager -n 5
```
Expected: línea `doors-ingest: {'palmetto': <N>, 'teq': <M>}` con `N` en miles (histórico Palmetto 2026) y `M` en cientos/miles (ventana Teq del poller). Exit 0.

- [ ] **Step 4: Habilitar el timer**

Run:
```bash
systemctl enable --now doors-ingest.timer
systemctl list-timers doors-ingest.timer --no-pager
```
Expected: el timer aparece listado con próximo disparo.

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -q -m "feat: doors-ingest entrypoint + systemd timer"
```

---

### Task 8: Verificación del backfill (datos reales en la DB)

**Files:**
- (solo verificación; sin cambios de código)

**Interfaces:**
- Consumes: `config.DB_PATH` poblada por Task 7.

- [ ] **Step 1: Verificar conteos, rango de fechas y presencia de las 2 sedes**

Run:
```bash
python3 - <<'PY'
import sqlite3
from doors_analytics import config
c = sqlite3.connect(config.DB_PATH)
print("total:", c.execute("SELECT COUNT(*) FROM events").fetchone()[0])
print("por sede:", c.execute(
    "SELECT sede, COUNT(*) FROM events GROUP BY sede").fetchall())
print("rango:", c.execute(
    "SELECT MIN(timestamp), MAX(timestamp) FROM events").fetchone())
print("palmetto con direction:", c.execute(
    "SELECT COUNT(*) FROM events WHERE source='palmetto' AND direction IS NOT NULL"
).fetchone()[0])
print("cursores:", c.execute(
    "SELECT source, device_id, last_index FROM ingest_state ORDER BY source").fetchall())
PY
```
Expected:
- `total` > 0; `por sede` incluye `('Palmetto', N)` y `('Tequendama', M)`.
- `MIN(timestamp)` >= `2026-01-01`.
- `palmetto con direction` > 0.
- Cursores presentes para `palmetto/222451671` y los serials Teq vistos.

- [ ] **Step 2: Verificar idempotencia en caliente (segundo run no crece)**

Run:
```bash
BEFORE=$(python3 -c "import sqlite3;from doors_analytics import config;print(sqlite3.connect(config.DB_PATH).execute('SELECT COUNT(*) FROM events').fetchone()[0])")
systemctl start doors-ingest.service
AFTER=$(python3 -c "import sqlite3;from doors_analytics import config;print(sqlite3.connect(config.DB_PATH).execute('SELECT COUNT(*) FROM events').fetchone()[0])")
echo "before=$BEFORE after=$AFTER (delta = eventos nuevos desde el último run)"
```
Expected: `after >= before`, y el delta es pequeño (solo eventos nuevos reales), nunca duplicación del total.

- [ ] **Step 3: Commit del README con el estado de verificación**

```bash
cd /root/uhppoted-dashboard
printf '\n## Estado Plan 1 (foundation)\nDatastore + ingesta operativos. `doors-ingest.timer` cada 10 min. Backfill Palmetto desde 2026-01-01; Teq = ventana del poller (cosecha profunda diferida al edge box).\n' >> README.md
git add -A && git commit -q -m "docs: verificación backfill Plan 1"
```

---

## Self-Review (hecho)

**1. Cobertura del spec:** §5 modelo de datos → Task 2 (5 tablas). §6 ingesta/colectores → Tasks 5–6. Idempotencia `(device_id, idx)` → Task 3. Backfill desde 2026-01-01 → Task 5 (filtro) + Task 7 (run) + Task 8 (verif). SQLite local → Global Constraints + Task 2. **Diferidos explícitos:** endpoint `/ingest` HTTP (Plan 4/edge box), cosecha profunda Teq por CLI (post-Plan 1), colector `teq-edge-push` (cuando exista el edge box) — todos con el esquema/cursores ya listos, sin rework. API de eventos/KPIs/CRUD y auth → Planes 2–4.

**2. Placeholders:** ninguno; todo el código y comandos son concretos.

**3. Consistencia de tipos/nombres:** contrato de evento normalizado (`device_id, idx, timestamp, card, door, door_name, granted, reason, direction, event_type, sede, source`) idéntico en colectores (Task 5), upsert (Task 3) y columnas de la tabla (Task 2). `get_cursor/set_cursor` firmas iguales en Tasks 4/6. `run_once` firma consistente en Tasks 6/7.
