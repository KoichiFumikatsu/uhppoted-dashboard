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
