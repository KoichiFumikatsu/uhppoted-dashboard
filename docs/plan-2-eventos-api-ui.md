# Histórico de Eventos: API + UI (uhppoted dashboard) — Plan 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exponer el histórico de eventos de `doors.db` (poblado por Plan 1) como una API de solo-lectura consultable por rango de fechas + filtros, con una UI web (tab Eventos) y export CSV.

**Architecture:** La lógica de consulta va en `doors_analytics/queries.py` (funciones puras, TDD). Un servicio HTTP de solo-lectura nuevo y decoupled (`doors-analytics-api`, stdlib `http.server`, `127.0.0.1:8447`) la sirve. nginx lo publica bajo `/analytics/`; una página estática `analytics/index.html` es la UI. **El control-plane (`schedule-manager`, panel httpd) NO se toca.**

**Tech Stack:** Python 3.8 stdlib (`sqlite3`, `http.server`, `urllib`, `csv`, `json`, `unittest`), nginx, systemd, HTML/JS vanilla.

## Global Constraints

- **Solo stdlib de Python 3.8.** NO pip/pytest/deps. Tests con `python3 -m unittest`.
- **Ejecución en el server:** `ssh root@192.168.12.25`, repo en `/root/uhppoted-dashboard/`. Autorar local + `scp`.
- **No modificar el control-plane:** nada de `schedule-manager` (:8446), `uhppoted-httpd`, ni servicios existentes.
- **Servicio nuevo:** `doors-analytics-api` = `/usr/local/bin/doors-analytics-api`, escucha `127.0.0.1:8447` (HTTP plano; nginx hace TLS), systemd `Type=simple Restart=on-failure` (imitar `schedule-manager.service`). Corre como root (igual patrón; `doors.db` es root-owned).
- **DB:** `/var/uhppoted/analytics/doors.db`. El servicio activa `PRAGMA journal_mode=WAL` al arrancar (evita "database is locked" con el writer `doors-ingest`) y usa `PRAGMA busy_timeout=5000`. Conexión fresca por request.
- **nginx vhost:** `/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf` (root:azcweb). Agregar `location /analytics/api/` (proxy a :8447) y `location /analytics/` (alias estático). Validar SIEMPRE con `nginx -t` antes de `systemctl reload nginx`.
- **Static UI:** `/home/azcweb/web/doors.azc.com.co/public_html/analytics/` (owner `azcweb:azcweb`).
- **Sin auth todavía** (se agrega en Plan 4, junto con el CRUD de placas). Consistente con `/schedules/api/` actual, que tampoco tiene.
- **Contrato de fila de evento** (lo que devuelve la API y consume la UI): `timestamp, sede, device_id, door, door_name, card, card_name (de card_persons, nullable), granted, reason, direction`.

---

## File Structure

```
/root/uhppoted-dashboard/
  doors_analytics/
    queries.py          # query_events(), events_to_csv() — TDD
  tests/
    test_queries.py
/usr/local/bin/doors-analytics-api          # servicio HTTP stdlib (:8447)
/etc/systemd/system/doors-analytics-api.service
/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf   # + 2 locations
/home/azcweb/web/doors.azc.com.co/public_html/analytics/index.html   # UI Eventos
```

**Nota UI:** hay un tab viejo "Eventos Teq" en `schedules/index.html` (solo Teq). La UI nueva `/analytics/` lo supera (5 placas + rango de fechas + CSV). NO se elimina el viejo en este plan (evitar tocar el `index.html` grande); queda para retiro posterior.

---

### Task 1: Lógica de consulta (`queries.py`)

**Files:**
- Create: `/root/uhppoted-dashboard/doors_analytics/queries.py`
- Test: `/root/uhppoted-dashboard/tests/test_queries.py`

**Interfaces:**
- Consumes: la tabla `events` y `card_persons` de Plan 1 (esquema `db.init_db`).
- Produces:
  - `query_events(conn, filters, page=1, page_size=100) -> {"rows": list[dict], "total": int, "page": int, "page_size": int, "pages": int}`. `filters` dict con claves opcionales `from, to` (fecha `YYYY-MM-DD`, inclusive por día), `device, card, door` (int), `sede` (str), `granted` ("0"/"1"). Orden `timestamp DESC, device_id, idx DESC`. Cada row incluye `card_name` (LEFT JOIN `card_persons`, `None` si no hay).
  - `events_to_csv(rows) -> str` (header + filas, campos del contrato).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_queries.py
import sqlite3
import unittest
from doors_analytics import db, queries


def _seed(conn):
    db.init_db(conn)
    evs = [
        # device, idx, ts, card, door, door_name, granted, reason, direction, sede, source
        (222451671, 1, "2026-03-01 07:10:00", 111, 1, "S1 Porteria", 1, 1, 1, "Palmetto", "palmetto"),
        (222451671, 2, "2026-03-01 18:30:00", 111, 1, "S1 Porteria", 1, 1, 2, "Palmetto", "palmetto"),
        (222451671, 3, "2026-03-02 08:00:00", 222, 1, "S1 Porteria", 0, 6, 1, "Palmetto", "palmetto"),
        (225088590, 900, "2026-03-02 09:00:00", 111, 1, "Teq .125 P1", 1, 1, None, "Tequendama", "teq"),
    ]
    conn.executemany(
        "INSERT INTO events (device_id, idx, timestamp, card, door, door_name, "
        "granted, reason, direction, sede, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)", evs)
    conn.execute("INSERT INTO card_persons (card, name) VALUES (?,?)", (111, "Juan Perez"))
    conn.commit()


class TestQueryEvents(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)

    def test_no_filters_returns_all_desc(self):
        r = queries.query_events(self.conn, {})
        self.assertEqual(r["total"], 4)
        # orden desc por timestamp: el más nuevo (2026-03-02 09:00) primero
        self.assertEqual(r["rows"][0]["timestamp"], "2026-03-02 09:00:00")

    def test_date_range_inclusive(self):
        r = queries.query_events(self.conn, {"from": "2026-03-01", "to": "2026-03-01"})
        self.assertEqual(r["total"], 2)  # solo los dos del 01

    def test_filter_by_sede_and_card(self):
        r = queries.query_events(self.conn, {"sede": "Tequendama"})
        self.assertEqual(r["total"], 1)
        r2 = queries.query_events(self.conn, {"card": 222})
        self.assertEqual(r2["total"], 1)

    def test_filter_granted(self):
        r = queries.query_events(self.conn, {"granted": "0"})
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["rows"][0]["card"], 222)

    def test_card_name_join(self):
        r = queries.query_events(self.conn, {"card": 111, "page_size": 1})
        self.assertEqual(r["rows"][0]["card_name"], "Juan Perez")
        r2 = queries.query_events(self.conn, {"card": 222})
        self.assertIsNone(r2["rows"][0]["card_name"])

    def test_pagination(self):
        r = queries.query_events(self.conn, {}, page=1, page_size=2)
        self.assertEqual(len(r["rows"]), 2)
        self.assertEqual(r["pages"], 2)
        self.assertEqual(r["page"], 1)

    def test_csv_has_header_and_rows(self):
        rows = queries.query_events(self.conn, {})["rows"]
        csv_text = queries.events_to_csv(rows)
        lines = [l for l in csv_text.splitlines() if l.strip()]
        self.assertEqual(lines[0], "timestamp,sede,device_id,door,door_name,card,card_name,granted,reason,direction")
        self.assertEqual(len(lines), 1 + 4)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_queries -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'doors_analytics.queries'`.

- [ ] **Step 3: Implementar `queries.py`**

```python
# doors_analytics/queries.py
import csv
import io

FIELDS = ["timestamp", "sede", "device_id", "door", "door_name",
          "card", "card_name", "granted", "reason", "direction"]


def _build_where(filters):
    clauses, params = [], []
    f = filters or {}
    if f.get("from"):
        clauses.append("substr(e.timestamp,1,10) >= ?"); params.append(f["from"])
    if f.get("to"):
        clauses.append("substr(e.timestamp,1,10) <= ?"); params.append(f["to"])
    if f.get("device"):
        clauses.append("e.device_id = ?"); params.append(int(f["device"]))
    if f.get("card"):
        clauses.append("e.card = ?"); params.append(int(f["card"]))
    if f.get("door") not in (None, ""):
        clauses.append("e.door = ?"); params.append(int(f["door"]))
    if f.get("sede"):
        clauses.append("e.sede = ?"); params.append(f["sede"])
    if f.get("granted") not in (None, ""):
        clauses.append("e.granted = ?"); params.append(int(f["granted"]))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def query_events(conn, filters, page=1, page_size=100):
    where, params = _build_where(filters)
    total = conn.execute("SELECT COUNT(*) FROM events e" + where, params).fetchone()[0]
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100000))
    offset = (page - 1) * page_size
    sql = (
        "SELECT e.timestamp, e.sede, e.device_id, e.door, e.door_name, "
        "e.card, cp.name AS card_name, e.granted, e.reason, e.direction "
        "FROM events e LEFT JOIN card_persons cp ON cp.card = e.card"
        + where +
        " ORDER BY e.timestamp DESC, e.device_id, e.idx DESC LIMIT ? OFFSET ?"
    )
    cur = conn.execute(sql, params + [page_size, offset])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    pages = (total + page_size - 1) // page_size if page_size else 0
    return {"rows": rows, "total": total, "page": page,
            "page_size": page_size, "pages": pages}


def events_to_csv(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=FIELDS, extrasaction="ignore",
                       lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return out.getvalue()
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_queries -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Correr la suite completa (no romper Plan 1)**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest discover -s tests`
Expected: OK (11 de Plan 1 + 7 nuevos = 18).

- [ ] **Step 6: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: queries.py (query_events + events_to_csv)"
```

---

### Task 2: Servicio HTTP `doors-analytics-api` + systemd

**Files:**
- Create: `/root/uhppoted-dashboard/service/doors-analytics-api` (fuente en el repo)
- Deploy: copiar a `/usr/local/bin/doors-analytics-api`
- Create: `/etc/systemd/system/doors-analytics-api.service`

**Interfaces:**
- Consumes: `doors_analytics.config`, `doors_analytics.queries`.
- Produces: HTTP en `127.0.0.1:8447`:
  - `GET /api/health` → `{"ok": true}`
  - `GET /api/events?from=&to=&device=&card=&door=&sede=&granted=&page=&page_size=` → JSON de `query_events`.
  - `GET /api/events.csv?<mismos filtros>` → `text/csv` adjunto (todas las filas del filtro, hasta 100000).

- [ ] **Step 1: Escribir el servicio**

```python
# service/doors-analytics-api
#!/usr/bin/env python3
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, "/root/uhppoted-dashboard")
from doors_analytics import config, queries


def _conn():
    c = sqlite3.connect(config.DB_PATH, timeout=5)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def _init_wal():
    c = _conn()
    try:
        c.execute("PRAGMA journal_mode=WAL")
    finally:
        c.close()


def _filters(qs):
    def one(k):
        v = qs.get(k, [None])[0]
        return v if v not in ("", None) else None
    return {k: one(k) for k in
            ("from", "to", "device", "card", "door", "sede", "granted")}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        try:
            if path == "/api/health":
                self._send(200, json.dumps({"ok": True}))
            elif path == "/api/events":
                page = int((qs.get("page", ["1"])[0]) or 1)
                page_size = int((qs.get("page_size", ["100"])[0]) or 100)
                conn = _conn()
                try:
                    res = queries.query_events(conn, _filters(qs), page, page_size)
                finally:
                    conn.close()
                self._send(200, json.dumps(res))
            elif path == "/api/events.csv":
                conn = _conn()
                try:
                    res = queries.query_events(conn, _filters(qs), 1, 100000)
                finally:
                    conn.close()
                self._send(200, queries.events_to_csv(res["rows"]),
                           "text/csv; charset=utf-8",
                           {"Content-Disposition": "attachment; filename=eventos.csv"})
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    _init_wal()
    HTTPServer(("127.0.0.1", 8447), Handler).serve_forever()
```

Deploy + unit:

```bash
scp service/doors-analytics-api  # a /root/uhppoted-dashboard/service/ (repo)
cp /root/uhppoted-dashboard/service/doors-analytics-api /usr/local/bin/doors-analytics-api
chmod +x /usr/local/bin/doors-analytics-api
```

```ini
# /etc/systemd/system/doors-analytics-api.service
[Unit]
Description=uhppoted dashboard - API de solo-lectura de eventos (127.0.0.1:8447)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/doors-analytics-api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Arrancar y verificar en localhost**

Run:
```bash
systemctl daemon-reload
systemctl enable --now doors-analytics-api.service
systemctl is-active doors-analytics-api.service
curl -s http://127.0.0.1:8447/api/health
echo
curl -s "http://127.0.0.1:8447/api/events?from=2026-07-01&to=2026-07-22&page_size=3" | python3 -m json.tool | head -20
echo "--- total del rango ---"
curl -s "http://127.0.0.1:8447/api/events?from=2026-07-01&to=2026-07-22&page_size=1" | python3 -c "import sys,json;d=json.load(sys.stdin);print('total:',d['total'],'pages:',d['pages'])"
echo "--- CSV (primeras 3 líneas) ---"
curl -s "http://127.0.0.1:8447/api/events.csv?sede=Tequendama&from=2026-07-01&to=2026-07-22" | head -3
```
Expected: `is-active` → `active`; health `{"ok": true}`; `/api/events` devuelve `{rows:[...], total:N>0, ...}` con filas reales de julio; CSV con header `timestamp,sede,...` + filas de Tequendama.

- [ ] **Step 3: Verificar WAL activado (no rompe al writer)**

Run: `ls -la /var/uhppoted/analytics/ | grep doors.db` — Expected: aparecen `doors.db-wal` y `doors.db-shm` (WAL activo).

- [ ] **Step 4: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: doors-analytics-api read-only service (:8447) + systemd"
```

---

### Task 3: nginx `/analytics/` + UI Eventos

**Files:**
- Modify: `/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf`
- Create: `/root/uhppoted-dashboard/ui/analytics-index.html` (fuente en el repo)
- Deploy: `/home/azcweb/web/doors.azc.com.co/public_html/analytics/index.html`

**Interfaces:**
- Consumes: `GET /analytics/api/events` y `/analytics/api/events.csv` (proxy a :8447).
- Produces: página `https://doors.azc.com.co/analytics/` con filtros de fecha + tabla + paginación + botón CSV.

- [ ] **Step 1: Agregar los dos `location` al vhost (backup primero)**

Run:
```bash
cp /home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf \
   /home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf.bak.analytics-$(date +%Y%m%d-%H%M%S)
```
Insertar estos dos bloques dentro del `server { ... }` (junto a los `location /schedules/...`), usando un editor o `sed`. El contenido a insertar:
```nginx
    location /analytics/api/ {
        proxy_pass http://127.0.0.1:8447/api/;
        proxy_set_header Host $host;
    }
    location /analytics/ {
        alias /home/azcweb/web/doors.azc.com.co/public_html/analytics/;
        index index.html;
    }
```
Método concreto (inserta antes de la línea `location /schedules/api/`):
```bash
python3 - <<'PY'
p = "/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf"
s = open(p).read()
anchor = "    location /schedules/api/ {"
block = (
    "    location /analytics/api/ {\n"
    "        proxy_pass http://127.0.0.1:8447/api/;\n"
    "        proxy_set_header Host $host;\n"
    "    }\n"
    "    location /analytics/ {\n"
    "        alias /home/azcweb/web/doors.azc.com.co/public_html/analytics/;\n"
    "        index index.html;\n"
    "    }\n"
)
assert anchor in s, "anchor not found"
assert "/analytics/api/" not in s, "ya insertado"
s = s.replace(anchor, block + anchor, 1)
open(p, "w").write(s)
print("insertado")
PY
```

- [ ] **Step 2: Validar y recargar nginx**

Run: `nginx -t && systemctl reload nginx`
Expected: `nginx: configuration file ... test is successful` y reload sin error. **Si `nginx -t` falla, restaurar el backup y parar.**

- [ ] **Step 3: Crear la UI (página Eventos autocontenida)**

Autorar en el repo `ui/analytics-index.html` con EXACTAMENTE este contenido:

```html
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Eventos de acceso — AZC</title>
<style>
  :root { --bg:#0f1720; --panel:#182430; --line:#2a3a49; --fg:#e6edf3; --mut:#9fb0c0; --accent:#3fa7ff; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:14px 20px; border-bottom:1px solid var(--line); font-size:18px; font-weight:600; }
  .filters { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; padding:16px 20px; }
  .filters label { display:flex; flex-direction:column; font-size:12px; color:var(--mut); gap:4px; }
  .filters input, .filters select { background:var(--panel); border:1px solid var(--line); color:var(--fg); padding:7px 9px; border-radius:6px; font-size:13px; }
  button { background:var(--accent); color:#04121f; border:none; padding:8px 14px; border-radius:6px; font-weight:600; cursor:pointer; font-size:13px; }
  button.sec { background:var(--panel); color:var(--fg); border:1px solid var(--line); }
  .wrap { padding:0 20px 24px; overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--mut); font-weight:600; position:sticky; top:0; background:var(--bg); }
  .deny { color:#ff7b72; }
  .pager { display:flex; gap:10px; align-items:center; padding:12px 20px; color:var(--mut); font-size:13px; }
  .badge { font-size:11px; padding:2px 7px; border-radius:10px; background:var(--panel); border:1px solid var(--line); }
</style>
</head>
<body>
<header>Eventos de acceso <span class="badge" id="count">—</span></header>
<div class="filters">
  <label>Desde<input type="date" id="from"></label>
  <label>Hasta<input type="date" id="to"></label>
  <label>Sede<select id="sede"><option value="">Todas</option><option>Palmetto</option><option>Tequendama</option></select></label>
  <label>Tarjeta<input type="text" id="card" inputmode="numeric" placeholder="nº"></label>
  <label>Puerta<input type="text" id="door" inputmode="numeric" placeholder="1-4"></label>
  <label>Acceso<select id="granted"><option value="">Todos</option><option value="1">Concedido</option><option value="0">Negado</option></select></label>
  <button id="apply">Filtrar</button>
  <button class="sec" id="csv">Exportar CSV</button>
</div>
<div class="wrap">
  <table>
    <thead><tr><th>Fecha/Hora</th><th>Sede</th><th>Puerta</th><th>Tarjeta</th><th>Nombre</th><th>Acceso</th><th>Dir.</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>
<div class="pager">
  <button class="sec" id="prev">‹ Anterior</button>
  <span id="pageinfo">—</span>
  <button class="sec" id="next">Siguiente ›</button>
</div>
<script>
const API = "/analytics/api";
let page = 1;
function fmtDate(d){ return d.toISOString().slice(0,10); }
function initDates(){
  const to = new Date(); const from = new Date(Date.now() - 6*864e5);
  document.getElementById("to").value = fmtDate(to);
  document.getElementById("from").value = fmtDate(from);
}
function params(extra){
  const p = new URLSearchParams();
  for (const k of ["from","to","sede","card","door","granted"]){
    const v = document.getElementById(k).value.trim();
    if (v) p.set(k, v);
  }
  for (const k in (extra||{})) p.set(k, extra[k]);
  return p.toString();
}
const DIR = {1:"Entra", 2:"Sale"};
async function load(){
  const res = await fetch(`${API}/events?${params({page, page_size:50})}`);
  const d = await res.json();
  const tb = document.getElementById("rows"); tb.innerHTML = "";
  for (const r of d.rows){
    const tr = document.createElement("tr");
    const g = r.granted ? "Concedido" : "<span class='deny'>Negado</span>";
    tr.innerHTML = `<td>${r.timestamp||""}</td><td>${r.sede||""}</td>`
      + `<td>${r.door_name||r.door||""}</td><td>${r.card||""}</td>`
      + `<td>${r.card_name||""}</td><td>${g}</td><td>${DIR[r.direction]||""}</td>`;
    tb.appendChild(tr);
  }
  document.getElementById("count").textContent = d.total + " eventos";
  document.getElementById("pageinfo").textContent = `Página ${d.page} de ${d.pages||1}`;
  document.getElementById("prev").disabled = d.page <= 1;
  document.getElementById("next").disabled = d.page >= (d.pages||1);
}
document.getElementById("apply").onclick = () => { page = 1; load(); };
document.getElementById("prev").onclick = () => { if (page>1){ page--; load(); } };
document.getElementById("next").onclick = () => { page++; load(); };
document.getElementById("csv").onclick = () => { window.location = `${API}/events.csv?${params()}`; };
initDates(); load();
</script>
</body>
</html>
```

Deploy:
```bash
mkdir -p /home/azcweb/web/doors.azc.com.co/public_html/analytics
scp ui/analytics-index.html  # a /root/uhppoted-dashboard/ui/ (repo)
cp /root/uhppoted-dashboard/ui/analytics-index.html /home/azcweb/web/doors.azc.com.co/public_html/analytics/index.html
chown -R azcweb:azcweb /home/azcweb/web/doors.azc.com.co/public_html/analytics
```

- [ ] **Step 4: Verificar end-to-end vía nginx (dominio público)**

Run:
```bash
echo "--- API por nginx ---"
curl -sk "https://doors.azc.com.co/analytics/api/events?from=2026-07-01&to=2026-07-22&page_size=1" | python3 -c "import sys,json;d=json.load(sys.stdin);print('total:',d['total'])"
echo "--- página HTML ---"
curl -sk -o /dev/null -w "HTTP %{http_code} ct=%{content_type}\n" https://doors.azc.com.co/analytics/
echo "--- CSV por nginx ---"
curl -sk "https://doors.azc.com.co/analytics/api/events.csv?from=2026-07-01&to=2026-07-22&sede=Palmetto" | head -2
```
Expected: `total:` > 0; página HTML `HTTP 200 ct=text/html`; CSV con header + al menos una fila.

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: nginx /analytics/ + UI Eventos (rango de fechas, filtros, CSV)"
```

---

## Self-Review (hecho)

**1. Cobertura del spec:** §8 Eventos (API `/api/events?from=&to=&...`, filtros, paginación, CSV) → Tasks 1–3. Lectura desde SQLite (no live) → Task 2. `card_name` name-ready (LEFT JOIN `card_persons`) → Task 1. WAL para lector concurrente (recomendación review Plan 1) → Task 2 Global Constraints. No-auth explícito (Plan 4) → Global Constraints. Control-plane intacto → servicio separado.

**2. Placeholders:** ninguno — todo el código (queries, servicio, unit, nginx snippet, HTML/JS, comandos) es concreto.

**3. Consistencia de tipos/nombres:** contrato de fila (`timestamp, sede, device_id, door, door_name, card, card_name, granted, reason, direction`) idéntico en `queries.FIELDS` (Task 1), el `SELECT` de `query_events` (Task 1), el CSV (Task 1) y la UI (Task 3). `query_events(conn, filters, page, page_size)` y `events_to_csv(rows)` con firmas consistentes entre Task 1 (definición), Task 2 (uso en el servicio) y los tests. Endpoints `/api/events`, `/api/events.csv`, `/api/health` consistentes entre servicio (Task 2), nginx (Task 3) y UI (Task 3).
