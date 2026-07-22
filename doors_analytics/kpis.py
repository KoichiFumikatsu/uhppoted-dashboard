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
            arrival = min(ins) if ins else None
            departure = max(outs) if outs else None
        else:
            times = sorted(_secs(e["timestamp"]) for e in evs)
            arrival, departure = (times[0], times[-1]) if times[0] != times[-1] else (None, None)
        out.append((sede, card, day, arrival, departure))
    return out


def arrival_departure(rows):
    per = _first_last_per_group(rows)
    acc = defaultdict(lambda: {"arr": [], "dep": []})
    for r in rows:
        acc[r["sede"]]  # asegura que toda sede aparezca aunque no tenga pares validos
    for sede, card, day, a, d in per:
        if a is not None:
            acc[sede]["arr"].append(a)
        if d is not None:
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
    hh, mm = threshold.split(":")[:2]
    th = int(hh) * 3600 + int(mm) * 60
    per = _first_last_per_group(rows)
    acc = defaultdict(lambda: {"late": 0, "total": 0})
    for sede, card, day, a, d in per:
        if a is None:
            continue
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
