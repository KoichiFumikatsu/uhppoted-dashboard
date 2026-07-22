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
