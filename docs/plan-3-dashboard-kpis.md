# Dashboard de KPIs de acceso (uhppoted dashboard) — Plan 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calcular y mostrar KPIs de acceso por sede sobre `doors.db`: primera entrada / última salida promedio, asistencia + llegadas tardías, picos horarios + volumen diario, top puertas + tarjetas — bajo `/analytics/` (misma Basic Auth de Plan 2).

**Architecture:** Cómputo puro en `doors_analytics/kpis.py` (TDD, sobre filas de eventos). El servicio `doors-analytics-api` (:8447) gana un endpoint `/api/kpis`. Una página estática nueva `analytics/dashboard.html` renderiza tiles + gráficos SVG vanilla (sin CDN), tema oscuro, con la paleta validada de la skill dataviz.

**Tech Stack:** Python 3.8 stdlib (`sqlite3`, `datetime`, `unittest`), HTML/JS vanilla + SVG.

## Global Constraints

- **Solo stdlib de Python 3.8.** NO pip/pytest/deps. Tests con `python3 -m unittest`. Ejecución en el server vía SSH; autorar local + `scp`.
- **No modificar el control-plane.** Solo se toca `doors_analytics/`, el servicio `doors-analytics-api` (agregar una ruta), y se agregan estáticos bajo `public_html/analytics/`.
- **DB:** `/var/uhppoted/analytics/doors.db` (solo lectura).
- **Base de KPIs:** solo eventos `granted=1 AND reason=1` (excluye botón de apertura `reason=20` y ruido).
- **Entrada/salida por sede:** Palmetto usa `direction` (1=entrada / 2=salida); Tequendama (sin `direction`) usa heurística **primer/último swipe del día por tarjeta**. La lógica auto-detecta: si el grupo (sede,card,día) tiene algún `direction`, lo usa; si no, primer/último.
- **Timestamps = local naive -05** (ya garantizado por Plan 1). Promedios de hora se calculan en segundos-desde-medianoche.
- **Auth:** la página vive bajo `/analytics/` → ya protegida por la Basic Auth de Plan 2 (nginx). NO se agrega auth nueva.
- **Paleta (dataviz validada, tema oscuro):** sede Palmetto=`#3987e5` (azul), Tequendama=`#008300` (verde) — par categórico validado. Magnitud de serie única = azul `#3987e5`. Estado *warning* (tardanzas) = `#fab219` con ícono+label. Tinta: primaria `#e6edf3`, secundaria/muted `#9fb0c0`. Superficie `#0f1720`/`#182430` (consistente con Eventos). Leyenda siempre presente para 2 series; texto en tinta, nunca en color de serie.

**Contrato de `compute_kpis` (lo que devuelve `/api/kpis` y consume la UI):**
```json
{
  "range": {"from": "...", "to": "...", "sede": null|"..."},
  "arrival_departure": {"Palmetto": {"arrival": "07:14", "departure": "18:22", "days": 42}, "Tequendama": {...}},
  "attendance": {"latest": {"Palmetto": 120, "Tequendama": 80}, "series": [{"date": "2026-07-01", "Palmetto": 120, "Tequendama": 80}, ...]},
  "late": {"threshold": "08:00", "Palmetto": {"late": 23, "total": 180, "pct": 12.8}, "Tequendama": {...}},
  "hourly": [{"hour": 0, "count": 0}, ... 24 entradas],
  "daily_volume": [{"date": "2026-07-01", "count": 640}, ...],
  "top_doors": [{"door": "(P) Porteria", "count": 5123}, ...],
  "top_cards": [{"card": 17059974, "name": null, "count": 88}, ...]
}
```

---

## File Structure

```
/root/uhppoted-dashboard/
  doors_analytics/kpis.py        # cómputo puro + fetch — TDD
  tests/test_kpis.py
  service/doors-analytics-api    # + ruta /api/kpis  (MODIFICAR)
  ui/analytics-dashboard.html    # nueva página
/home/azcweb/web/doors.azc.com.co/public_html/analytics/dashboard.html
  + link "Dashboard" <-> "Eventos" en ambas páginas
```

---

### Task 1: Cómputo de KPIs (`kpis.py`)

**Files:**
- Create: `/root/uhppoted-dashboard/doors_analytics/kpis.py`
- Test: `/root/uhppoted-dashboard/tests/test_kpis.py`

**Interfaces:**
- Produces:
  - `fetch_rows(conn, filters) -> list[dict]` — eventos `granted=1 AND reason=1` con filtros `from/to/sede` (mismas fechas inclusivas que Plan 2); cada row: `card, sede, timestamp, door, door_name, direction`.
  - `compute_kpis(conn, filters, late_threshold="08:00") -> dict` (contrato de arriba).
  - Helpers puros usados por los tests: `arrival_departure(rows)`, `attendance(rows)`, `late_arrivals(rows, threshold)`, `hourly(rows)`, `daily_volume(rows)`, `top_doors(rows, n=8)`, `top_cards(rows, n=10)`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_kpis.py
import sqlite3
import unittest
from doors_analytics import db, kpis


def _seed(conn):
    db.init_db(conn)
    rows = [
        # Palmetto con direction: card 111 el 2026-03-01 entra 07:00 (dir1) sale 18:00 (dir2)
        (222451671, 1, "2026-03-01 07:00:00", 111, 1, "(P) Porteria", 1, 1, 1, "Palmetto", "palmetto"),
        (222451671, 2, "2026-03-01 18:00:00", 111, 1, "(P) Porteria", 1, 1, 2, "Palmetto", "palmetto"),
        # card 222 entra 09:00 (tarde) sale 17:00
        (222451671, 3, "2026-03-01 09:00:00", 222, 1, "(P) Porteria", 1, 1, 1, "Palmetto", "palmetto"),
        (222451671, 4, "2026-03-01 17:00:00", 222, 1, "(P) Porteria", 1, 1, 2, "Palmetto", "palmetto"),
        # ruido: reason 20 (botón) NO cuenta; granted 0 NO cuenta
        (222451671, 5, "2026-03-01 12:00:00", 111, 1, "(P) Porteria", 1, 20, 1, "Palmetto", "palmetto"),
        (222451671, 6, "2026-03-01 06:30:00", 333, 1, "(P) Porteria", 0, 6, 1, "Palmetto", "palmetto"),
        # Teq sin direction: card 111 el 2026-03-01, swipes 08:00 y 16:00 -> llegada 08:00, salida 16:00
        (225088590, 90, "2026-03-01 08:00:00", 111, 1, "Teq .125 P1", 1, 1, None, "Tequendama", "teq"),
        (225088590, 91, "2026-03-01 16:00:00", 111, 2, "Teq .125 P2", 1, 1, None, "Tequendama", "teq"),
    ]
    conn.executemany(
        "INSERT INTO events (device_id, idx, timestamp, card, door, door_name, "
        "granted, reason, direction, sede, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


class TestKpis(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)

    def test_fetch_excludes_noise(self):
        rows = kpis.fetch_rows(self.conn, {})
        # 4 Palmetto validos (idx 1-4) + 2 Teq = 6; excluye reason20 (idx5) y granted0 (idx6)
        self.assertEqual(len(rows), 6)

    def test_arrival_departure_palmetto_uses_direction(self):
        rows = kpis.fetch_rows(self.conn, {})
        ad = kpis.arrival_departure(rows)
        # Palmetto: llegadas 07:00 y 09:00 -> prom 08:00 ; salidas 18:00 y 17:00 -> prom 17:30
        self.assertEqual(ad["Palmetto"]["arrival"], "08:00")
        self.assertEqual(ad["Palmetto"]["departure"], "17:30")

    def test_arrival_departure_teq_uses_first_last(self):
        rows = kpis.fetch_rows(self.conn, {})
        ad = kpis.arrival_departure(rows)
        # Teq card111: primer swipe 08:00 = llegada, ultimo 16:00 = salida
        self.assertEqual(ad["Tequendama"]["arrival"], "08:00")
        self.assertEqual(ad["Tequendama"]["departure"], "16:00")

    def test_late_arrivals_threshold(self):
        rows = kpis.fetch_rows(self.conn, {})
        late = kpis.late_arrivals(rows, "08:00")
        # Palmetto: 2 llegadas (07:00, 09:00); >08:00 = 1 (la de 09:00)
        self.assertEqual(late["Palmetto"]["late"], 1)
        self.assertEqual(late["Palmetto"]["total"], 2)

    def test_attendance_unique_cards(self):
        rows = kpis.fetch_rows(self.conn, {})
        att = kpis.attendance(rows)
        # 2026-03-01: Palmetto tarjetas unicas {111,222}=2 ; Teq {111}=1
        self.assertEqual(att["latest"]["Palmetto"], 2)
        self.assertEqual(att["latest"]["Tequendama"], 1)

    def test_hourly_has_24_buckets(self):
        rows = kpis.fetch_rows(self.conn, {})
        h = kpis.hourly(rows)
        self.assertEqual(len(h), 24)
        self.assertEqual(h[7]["count"], 1)  # un evento a las 07:xx (Palmetto idx1)

    def test_top_doors_desc(self):
        rows = kpis.fetch_rows(self.conn, {})
        td = kpis.top_doors(rows)
        self.assertEqual(td[0]["door"], "(P) Porteria")  # 4 eventos, el mas usado

    def test_compute_kpis_shape(self):
        k = kpis.compute_kpis(self.conn, {}, "08:00")
        for key in ("range", "arrival_departure", "attendance", "late",
                    "hourly", "daily_volume", "top_doors", "top_cards"):
            self.assertIn(key, k)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_kpis -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'doors_analytics.kpis'`.

- [ ] **Step 3: Implementar `kpis.py`**

```python
# doors_analytics/kpis.py
from collections import defaultdict


def _secs(ts):
    # ts = "YYYY-MM-DD HH:MM:SS" (naive local) -> segundos desde medianoche
    hh, mm, ss = ts[11:13], ts[14:16], ts[17:19]
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def _hhmm(secs):
    secs = int(round(secs))
    return "%02d:%02d" % (secs // 3600, (secs % 3600) // 60)


def fetch_rows(conn, filters):
    clauses = ["granted = 1", "reason = 1"]
    params = []
    f = filters or {}
    if f.get("from"):
        clauses.append("substr(timestamp,1,10) >= ?"); params.append(f["from"])
    if f.get("to"):
        clauses.append("substr(timestamp,1,10) <= ?"); params.append(f["to"])
    if f.get("sede"):
        clauses.append("sede = ?"); params.append(f["sede"])
    sql = ("SELECT card, sede, timestamp, door, door_name, direction "
           "FROM events WHERE " + " AND ".join(clauses))
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _first_last_per_group(rows):
    # agrupa por (sede, card, dia) -> (arrival_secs, departure_secs)
    groups = defaultdict(list)
    for r in rows:
        key = (r["sede"], r["card"], r["timestamp"][:10])
        groups[key].append(r)
    out = []
    for (sede, card, day), evs in groups.items():
        dirs = [e for e in evs if e.get("direction") in (1, 2)]
        if dirs:
            ins = [_secs(e["timestamp"]) for e in dirs if e["direction"] == 1]
            outs = [_secs(e["timestamp"]) for e in dirs if e["direction"] == 2]
            arrival = min(ins) if ins else min(_secs(e["timestamp"]) for e in evs)
            departure = max(outs) if outs else max(_secs(e["timestamp"]) for e in evs)
        else:
            times = [_secs(e["timestamp"]) for e in evs]
            arrival, departure = min(times), max(times)
        out.append((sede, card, day, arrival, departure))
    return out


def arrival_departure(rows):
    per = _first_last_per_group(rows)
    acc = defaultdict(lambda: {"arr": [], "dep": []})
    for sede, card, day, a, d in per:
        acc[sede]["arr"].append(a)
        acc[sede]["dep"].append(d)
    res = {}
    for sede, v in acc.items():
        res[sede] = {
            "arrival": _hhmm(sum(v["arr"]) / len(v["arr"])) if v["arr"] else None,
            "departure": _hhmm(sum(v["dep"]) / len(v["dep"])) if v["dep"] else None,
            "days": len(v["arr"]),
        }
    return res


def late_arrivals(rows, threshold="08:00"):
    th = int(threshold[:2]) * 3600 + int(threshold[3:5]) * 60
    per = _first_last_per_group(rows)
    acc = defaultdict(lambda: {"late": 0, "total": 0})
    for sede, card, day, a, d in per:
        acc[sede]["total"] += 1
        if a > th:
            acc[sede]["late"] += 1
    res = {}
    for sede, v in acc.items():
        pct = round(100.0 * v["late"] / v["total"], 1) if v["total"] else 0.0
        res[sede] = {"late": v["late"], "total": v["total"], "pct": pct}
    return res


def attendance(rows):
    # por (sede, dia) tarjetas unicas
    per_day = defaultdict(lambda: defaultdict(set))
    for r in rows:
        per_day[r["timestamp"][:10]][r["sede"]].add(r["card"])
    days = sorted(per_day.keys())
    sedes = sorted({r["sede"] for r in rows})
    series = []
    for day in days:
        entry = {"date": day}
        for s in sedes:
            entry[s] = len(per_day[day].get(s, set()))
        series.append(entry)
    latest = {}
    if days:
        last = days[-1]
        for s in sedes:
            latest[s] = len(per_day[last].get(s, set()))
    return {"latest": latest, "series": series}


def hourly(rows):
    buckets = [0] * 24
    for r in rows:
        buckets[int(r["timestamp"][11:13])] += 1
    return [{"hour": h, "count": buckets[h]} for h in range(24)]


def daily_volume(rows):
    per = defaultdict(int)
    for r in rows:
        per[r["timestamp"][:10]] += 1
    return [{"date": d, "count": per[d]} for d in sorted(per.keys())]


def top_doors(rows, n=8):
    per = defaultdict(int)
    for r in rows:
        per[r.get("door_name") or ("Puerta %s" % r.get("door"))] += 1
    ordered = sorted(per.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{"door": k, "count": v} for k, v in ordered]


def top_cards(rows, n=10):
    per = defaultdict(int)
    for r in rows:
        per[r["card"]] += 1
    ordered = sorted(per.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{"card": k, "name": None, "count": v} for k, v in ordered]


def compute_kpis(conn, filters, late_threshold="08:00"):
    rows = fetch_rows(conn, filters)
    f = filters or {}
    return {
        "range": {"from": f.get("from"), "to": f.get("to"), "sede": f.get("sede")},
        "arrival_departure": arrival_departure(rows),
        "attendance": attendance(rows),
        "late": dict(late_arrivals(rows, late_threshold), threshold=late_threshold),
        "hourly": hourly(rows),
        "daily_volume": daily_volume(rows),
        "top_doors": top_doors(rows),
        "top_cards": top_cards(rows),
    }
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_kpis -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Suite completa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest discover -s tests`
Expected: OK (18 previos + 8 = 26).

- [ ] **Step 6: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: kpis.py (arrival/departure, attendance, late, hourly, volume, top doors/cards)"
```

---

### Task 2: Endpoint `/api/kpis` en el servicio

**Files:**
- Modify: `/root/uhppoted-dashboard/service/doors-analytics-api` (agregar ruta), redeploy a `/usr/local/bin/`

**Interfaces:**
- Consumes: `doors_analytics.kpis`.
- Produces: `GET /api/kpis?from=&to=&sede=&late_threshold=` → JSON de `compute_kpis`.

- [ ] **Step 1: Agregar el import y la ruta**

En `service/doors-analytics-api`, en el import de `doors_analytics` agregar `kpis`:
```python
from doors_analytics import config, queries, kpis
```
Y agregar una rama nueva en `do_GET` (después de la de `/api/events.csv`, antes del `else`):
```python
            elif path == "/api/kpis":
                lt = (qs.get("late_threshold", ["08:00"])[0]) or "08:00"
                conn = _conn()
                try:
                    res = kpis.compute_kpis(conn, _filters(qs), lt)
                finally:
                    conn.close()
                self._send(200, json.dumps(res))
```

- [ ] **Step 2: Redeploy y reiniciar el servicio**

```bash
cp /root/uhppoted-dashboard/service/doors-analytics-api /usr/local/bin/doors-analytics-api
chmod +x /usr/local/bin/doors-analytics-api
systemctl restart doors-analytics-api.service
systemctl is-active doors-analytics-api.service
```
Expected: `active`.

- [ ] **Step 3: Verificar con datos reales (localhost)**

Run:
```bash
curl -s "http://127.0.0.1:8447/api/kpis?from=2026-07-01&to=2026-07-22" | python3 -m json.tool | head -40
```
Expected: JSON con `arrival_departure` (Palmetto con horas reales; Tequendama si hay datos), `attendance.latest`, `late`, `hourly` (24), `daily_volume`, `top_doors` con `(P) Porteria` arriba.

- [ ] **Step 4: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: /api/kpis endpoint en doors-analytics-api"
```

---

### Task 3: Página Dashboard + navegación

**Files:**
- Create: `/root/uhppoted-dashboard/ui/analytics-dashboard.html`, deploy a `public_html/analytics/dashboard.html`
- Modify: `public_html/analytics/index.html` (link a Dashboard) y la nueva page (link a Eventos)

**Interfaces:**
- Consumes: `GET /analytics/api/kpis`.
- Produces: `https://doors.azc.com.co/analytics/dashboard.html` con tiles + gráficos SVG.

- [ ] **Step 1: Crear la página Dashboard**

Autorar `ui/analytics-dashboard.html` con EXACTAMENTE este contenido (tema oscuro, paleta dataviz validada, SVG vanilla):

```html
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Dashboard de accesos — AZC</title>
<style>
  :root{
    --bg:#0f1720; --panel:#182430; --line:#2a3a49; --ink:#e6edf3; --mut:#9fb0c0;
    --palmetto:#3987e5; --teq:#008300; --seq:#3987e5; --warn:#fab219;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
  header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center}
  header b{font-size:18px}
  header a{color:var(--mut);text-decoration:none;font-size:13px}
  header a:hover{color:var(--ink)}
  .filters{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;padding:14px 20px}
  .filters label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:4px}
  .filters input,.filters select{background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:7px 9px;border-radius:6px;font-size:13px}
  button{background:var(--palmetto);color:#04121f;border:none;padding:8px 14px;border-radius:6px;font-weight:600;cursor:pointer;font-size:13px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;padding:6px 20px 20px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
  .card h3{margin:0 0 8px;font-size:12px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
  .tile{display:flex;justify-content:space-between;align-items:baseline;margin:6px 0}
  .tile .lbl{font-size:13px;color:var(--mut);display:flex;align-items:center;gap:6px}
  .tile .val{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block}
  .wide{grid-column:1/-1}
  .legend{display:flex;gap:16px;font-size:12px;color:var(--mut);margin-bottom:8px}
  .legend span{display:flex;align-items:center;gap:6px}
  svg{width:100%;height:auto;display:block}
  .bar-lbl{fill:var(--mut);font-size:10px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  td,th{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--mut);font-weight:600}
  .warnpill{color:var(--warn);font-weight:700}
</style>
</head>
<body>
<header>
  <b>Dashboard de accesos</b>
  <a href="/analytics/">‹ Eventos</a>
</header>
<div class="filters">
  <label>Desde<input type="date" id="from"></label>
  <label>Hasta<input type="date" id="to"></label>
  <label>Sede<select id="sede"><option value="">Todas</option><option>Palmetto</option><option>Tequendama</option></select></label>
  <label>Umbral tardanza<input type="time" id="lt" value="08:00"></label>
  <button id="apply">Actualizar</button>
</div>
<div class="grid" id="grid"></div>
<script>
const API="/analytics/api";
const C={Palmetto:"#3987e5",Tequendama:"#008300"};
function d(id){return document.getElementById(id)}
function fmt(x){return new Date(x).toISOString().slice(0,10)}
function initDates(){ d("to").value=fmt(Date.now()); d("from").value=fmt(Date.now()-29*864e5); }
function el(tag,attrs,txt){const e=document.createElementNS(tag==="svg"||["rect","g","text","line","polyline","circle"].includes(tag)?"http://www.w3.org/2000/svg":null,tag);for(const k in(attrs||{}))e.setAttribute(k,attrs[k]);if(txt!=null)e.textContent=txt;return e}
function card(title,wide){const c=document.createElement("div");c.className="card"+(wide?" wide":"");const h=document.createElement("h3");h.textContent=title;c.appendChild(h);return c}
function tile(parent,label,value,color,warn){
  const t=document.createElement("div");t.className="tile";
  const l=document.createElement("span");l.className="lbl";
  if(color){const dot=document.createElement("span");dot.className="dot";dot.style.background=color;l.appendChild(dot)}
  l.appendChild(document.createTextNode(label));
  const v=document.createElement("span");v.className="val"+(warn?" warnpill":"");v.textContent=value;
  t.appendChild(l);t.appendChild(v);parent.appendChild(t);
}
// barras verticales (magnitud, un solo tono azul)
function barsV(data,xkey,ykey,h){
  const W=Math.max(data.length*26,300),H=h||140,pad=24;
  const max=Math.max(1,...data.map(r=>r[ykey]));
  const svg=el("svg",{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:"none"});
  const bw=(W-pad)/data.length*0.7;
  data.forEach((r,i)=>{
    const x=pad+i*((W-pad)/data.length), bh=(r[ykey]/max)*(H-pad-14);
    const rect=el("rect",{x:x, y:H-pad-bh, width:bw, height:bh, rx:3, fill:"#3987e5"});
    rect.appendChild(el("title",{},`${r[xkey]}: ${r[ykey]}`));
    svg.appendChild(rect);
    if(i%Math.ceil(data.length/12||1)===0) svg.appendChild(el("text",{x:x,y:H-8,class:"bar-lbl"},String(r[xkey])));
  });
  return svg;
}
// barras horizontales (top puertas)
function barsH(data,klabel,kval){
  const H=data.length*24+8,W=320,max=Math.max(1,...data.map(r=>r[kval]));
  const svg=el("svg",{viewBox:`0 0 ${W} ${H}`});
  data.forEach((r,i)=>{
    const bw=(r[kval]/max)*(W-140);
    svg.appendChild(el("text",{x:0,y:i*24+15,class:"bar-lbl"},String(r[klabel]).slice(0,20)));
    const rect=el("rect",{x:130,y:i*24+4,width:Math.max(bw,1),height:14,rx:3,fill:"#3987e5"});
    rect.appendChild(el("title",{},`${r[klabel]}: ${r[kval]}`));
    svg.appendChild(rect);
    svg.appendChild(el("text",{x:130+Math.max(bw,1)+5,y:i*24+15,class:"bar-lbl"},String(r[kval])));
  });
  return svg;
}
// lineas de asistencia por sede (2 categoricas)
function linesMulti(series,sedes){
  const W=Math.max(series.length*18,320),H=160,pad=24;
  const allv=series.flatMap(p=>sedes.map(s=>p[s]||0));
  const max=Math.max(1,...allv);
  const svg=el("svg",{viewBox:`0 0 ${W} ${H}`});
  sedes.forEach(s=>{
    const pts=series.map((p,i)=>{
      const x=pad+i*((W-pad)/Math.max(series.length-1,1));
      const y=H-pad-((p[s]||0)/max)*(H-pad-10);
      return `${x},${y}`;
    }).join(" ");
    svg.appendChild(el("polyline",{points:pts,fill:"none",stroke:C[s]||"#3987e5","stroke-width":2}));
  });
  return svg;
}
async function load(){
  const p=new URLSearchParams();
  for(const k of ["from","to","sede"]){const v=d(k).value.trim();if(v)p.set(k,v)}
  p.set("late_threshold",d("lt").value||"08:00");
  const k=await (await fetch(`${API}/kpis?${p}`)).json();
  const g=d("grid");g.innerHTML="";
  const sedes=Object.keys(k.arrival_departure);
  // 1) Llegada/salida promedio
  const c1=card("Llegada / salida promedio");
  sedes.forEach(s=>{ tile(c1,`${s} — llegada`,k.arrival_departure[s].arrival||"—",C[s]);
                     tile(c1,`${s} — salida`,k.arrival_departure[s].departure||"—",C[s]); });
  g.appendChild(c1);
  // 2) Asistencia (ultimo dia)
  const c2=card("Asistencia (último día)");
  Object.keys(k.attendance.latest).forEach(s=>tile(c2,s,k.attendance.latest[s],C[s]));
  g.appendChild(c2);
  // 3) Tardanzas
  const c3=card(`Llegadas tardías (> ${k.late.threshold})`);
  sedes.forEach(s=>{ const L=k.late[s]; if(L) tile(c3,s,`${L.late}/${L.total} (${L.pct}%)`,null,L.pct>=20); });
  g.appendChild(c3);
  // 4) Pico horario
  const c4=card("Accesos por hora del día",true);
  c4.appendChild(barsV(k.hourly,"hour","count",150));
  g.appendChild(c4);
  // 5) Volumen diario
  const c5=card("Volumen diario",true);
  c5.appendChild(barsV(k.daily_volume,"date","count",150));
  g.appendChild(c5);
  // 6) Asistencia por sede (lineas)
  if(k.attendance.series.length){
    const c6=card("Asistencia por día y sede",true);
    const leg=document.createElement("div");leg.className="legend";
    sedes.forEach(s=>{leg.innerHTML+=`<span><span class="dot" style="background:${C[s]}"></span>${s}</span>`});
    c6.appendChild(leg);
    c6.appendChild(linesMulti(k.attendance.series,sedes));
    g.appendChild(c6);
  }
  // 7) Top puertas
  const c7=card("Puertas más usadas");
  c7.appendChild(barsH(k.top_doors,"door","count"));
  g.appendChild(c7);
  // 8) Top tarjetas
  const c8=card("Tarjetas más activas");
  const tb=document.createElement("table");
  tb.innerHTML="<tr><th>Tarjeta</th><th>Nombre</th><th>Accesos</th></tr>";
  k.top_cards.forEach(r=>{const tr=document.createElement("tr");
    [r.card,r.name||"",r.count].forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.appendChild(td)});
    tb.appendChild(tr)});
  c8.appendChild(tb);g.appendChild(c8);
}
d("apply").onclick=load;
initDates();load();
</script>
</body>
</html>
```

Deploy:
```bash
scp ui/analytics-dashboard.html  # a /root/uhppoted-dashboard/ui/ (repo)
cp /root/uhppoted-dashboard/ui/analytics-dashboard.html /home/azcweb/web/doors.azc.com.co/public_html/analytics/dashboard.html
chown azcweb:azcweb /home/azcweb/web/doors.azc.com.co/public_html/analytics/dashboard.html
```

- [ ] **Step 2: Link "Dashboard" desde Eventos**

En `public_html/analytics/index.html`, dentro del `<header>...</header>`, agregar un link a Dashboard justo después del `<span ... id="count">`:
```bash
python3 - <<'PY'
p="/home/azcweb/web/doors.azc.com.co/public_html/analytics/index.html"
s=open(p).read()
if 'href="/analytics/dashboard.html"' not in s:
    s=s.replace('</header>', '  <a href="/analytics/dashboard.html" style="color:#9fb0c0;text-decoration:none;font-size:13px;margin-left:16px">Dashboard ›</a>\n</header>', 1)
    open(p,"w").write(s); print("link agregado")
else: print("ya estaba")
PY
```
(Copiar el mismo cambio al fuente del repo `ui/analytics-index.html` para mantener paridad, y `chown azcweb`.)

- [ ] **Step 3: Verificar end-to-end (con credenciales)**

Run (usar la credencial Basic Auth de Plan 2):
```bash
U=azcdoors; P='<password>'
echo "--- API kpis por nginx ---"
curl -sk -u "$U:$P" "https://doors.azc.com.co/analytics/api/kpis?from=2026-07-01&to=2026-07-22" | python3 -c "import sys,json;k=json.load(sys.stdin);print('arrival_departure:',k['arrival_departure']);print('top_doors[0]:',k['top_doors'][0] if k['top_doors'] else None)"
echo "--- página dashboard ---"
curl -sk -u "$U:$P" -o /dev/null -w "HTTP %{http_code} ct=%{content_type}\n" https://doors.azc.com.co/analytics/dashboard.html
echo "--- sin cred = 401 ---"
curl -sk -o /dev/null -w "HTTP %{http_code}\n" https://doors.azc.com.co/analytics/dashboard.html
```
Expected: kpis con `arrival_departure` real (Palmetto con horas), `top_doors[0]` = Portería; dashboard HTML 200; sin cred 401 (heredó la auth de `/analytics/`).

- [ ] **Step 4: Render visual (mirar, no solo curl)**

Abrir `https://doors.azc.com.co/analytics/dashboard.html` en el browser (con la credencial) y verificar: tiles de llegada/salida por sede con su color, gráficos de barras horarias y volumen sin colisiones/overflow, líneas de asistencia con leyenda de 2 sedes, tabla top tarjetas. (O screenshot con la herramienta de browser disponible.)

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: página Dashboard de KPIs (tiles + gráficos SVG) + nav Eventos<->Dashboard"
```

---

## Self-Review (hecho)

**1. Cobertura del spec (§9):** primera entrada/última salida promedio por sede (direction Palmetto / heurística Teq) → `arrival_departure` (Task 1) + tiles (Task 3). Asistencia diaria → `attendance`; llegadas tardías con umbral configurable → `late_arrivals` + input `late_threshold`. Pico horario → `hourly`; tendencia de volumen → `daily_volume`. Top puertas + tarjetas → `top_doors`/`top_cards`. Filtro `granted=1 AND reason=1` → `fetch_rows`. KPIs por nº de tarjeta con `name` nullable (name-ready) → `top_cards`. Chart self-contained → SVG vanilla (sin CDN; sustituye "Chart.js embebido" del spec por SVG más liviano y sin dependencia — misma intención autocontenida). Bajo `/analytics/` = auth heredada.

**2. Placeholders:** ninguno (salvo `<password>` en el comando de verificación, que es la credencial real de Plan 2 que el operador ya tiene). Todo el código concreto.

**3. Consistencia de tipos/nombres:** contrato `compute_kpis` idéntico entre Task 1 (return), Task 2 (endpoint) y Task 3 (UI consume `arrival_departure/attendance/late/hourly/daily_volume/top_doors/top_cards`). `fetch_rows(conn, filters)` y helpers con firmas usadas consistentemente. Colores de sede (`#3987e5` Palmetto / `#008300` Teq) idénticos en Global Constraints y en la UI (`C`).
